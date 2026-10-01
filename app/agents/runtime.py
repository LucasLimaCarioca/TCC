"""Um event loop SPADE por processo, com inicialização e encerramento explícitos."""

import asyncio
import concurrent.futures
import multiprocessing
import tempfile
import threading

from app.agents.base_agent import AgentTimeout, AgentUnavailable
from app.agents.config import RuntimeConfig
from app.agents.protocol import JIDS, ONTOLOGIES
from app.agents.xmpp_server import serve


class AgentRuntime:
    _ownership = threading.Lock()

    def __init__(self, app, config=None):
        self.app = app
        self.config = config or RuntimeConfig.from_mapping(app.config)
        self.agents = {}
        self.running = False
        self.loop = None
        self.thread = None
        self.server = None
        self._mutex = threading.Lock()

    def start(self):
        with self._mutex:
            if self.running:
                return self
            if self.thread and self.thread.is_alive():
                raise AgentUnavailable("Runtime ainda em inicialização ou encerramento.")
            if not self._ownership.acquire(blocking=False):
                raise AgentUnavailable("Já existe um runtime SPADE neste processo.")
            self._ready = concurrent.futures.Future()
            self.thread = threading.Thread(target=self._run_loop, name="spade-runtime", daemon=True)
            self.thread.start()
            try:
                self._ready.result(timeout=self.config.startup_timeout + 3)
            except (Exception, KeyboardInterrupt):
                self.stop()
                raise AgentUnavailable(
                    "Falha ao iniciar SPADE/XMPP. Verifique as portas e a configuração local."
                ) from None
            return self

    def _run_loop(self):
        # STARTTLS do cliente Slixmpp/PyJabber usa o loop padrão. SPADE pode
        # instalar uvloop globalmente ao criar Container; não herdar essa troca.
        self.loop = asyncio.SelectorEventLoop()
        asyncio.set_event_loop(self.loop)
        self._stopping = asyncio.Event()
        try:
            self.loop.run_until_complete(self._lifecycle())
        finally:
            self.running = False
            tasks = asyncio.all_tasks(self.loop)
            for task in tasks:
                task.cancel()
            self.loop.run_until_complete(asyncio.gather(*tasks, return_exceptions=True))
            self.loop.run_until_complete(self.loop.shutdown_asyncgens())
            self.loop.run_until_complete(self.loop.shutdown_default_executor())
            self.loop.close()
            self._ownership.release()

    async def _lifecycle(self):
        try:
            async with asyncio.timeout(self.config.startup_timeout):
                await self._start_server()
                from spade.container import Container
                from app.agents.atendimento_agent import AtendimentoSPADEAgent
                from app.agents.estoque_agent import EstoqueAgent
                from app.agents.previsao_agent import PrevisaoAgent

                # SPADE Container é singleton; vincular ao loop desta execução.
                policy = asyncio.get_event_loop_policy()
                try:
                    container = Container()
                finally:
                    asyncio.set_event_loop_policy(policy)
                container.loop = asyncio.get_running_loop()
                port = self.config.client_port
                passwords = self.config.passwords
                self.agents = {
                    "atendimento": AtendimentoSPADEAgent(passwords["atendimento"], self.app, port, self._audit),
                    "estoque": EstoqueAgent(passwords["estoque"], port, self._audit),
                    "previsao": PrevisaoAgent(passwords["previsao"], port, self._audit),
                }
                for agent in self.agents.values():
                    self.startup_stage = agent.role
                    await agent.start()
            self.running = True
            self._ready.set_result(None)
            await self._stopping.wait()
        except Exception as error:
            self.failure_type = type(error).__name__
            self.app.logger.error("Falha SPADE na etapa %s (%s).",
                                  getattr(self, "startup_stage", "server"), self.failure_type)
            if not self._ready.done():
                self._ready.set_exception(AgentUnavailable("Inicialização incompleta."))
            else:
                self.app.logger.error("Runtime SPADE encerrado por falha interna.")
        finally:
            self.running = False
            await self._shutdown()

    async def _start_server(self):
        context = multiprocessing.get_context("spawn")
        self._server_ready = context.Event()
        self._server_stopping = context.Event()
        self._certificates = tempfile.TemporaryDirectory(prefix="tcc-xmpp-")
        self.server = context.Process(target=serve, args=(
            self.config.client_port, self.config.server_port, self._certificates.name,
            self._server_ready, self._server_stopping,
        ), name="tcc-xmpp", daemon=True)
        self.server.start()
        while not self._server_ready.is_set():
            if not self.server.is_alive():
                raise AgentUnavailable("Servidor XMPP local indisponível.")
            await asyncio.sleep(0.05)

    async def _shutdown(self):
        from spade.container import Container

        for agent in self.agents.values():
            try:
                await asyncio.wait_for(agent.stop(), timeout=2)
            except Exception:
                self.app.logger.warning("Falha ao desconectar um agente durante o encerramento.")
            finally:
                Container().unregister(str(agent.jid))
        if self.server and self.server.pid:
            self._server_stopping.set()
            await asyncio.to_thread(self.server.join, 2)
            if self.server.is_alive():
                self.server.terminate()
                await asyncio.to_thread(self.server.join, 2)
            if self.server.is_alive():
                self.server.kill()
                await asyncio.to_thread(self.server.join, 2)
        if hasattr(self, "_certificates"):
            self._certificates.cleanup()

    def stop(self):
        if self.loop and not self.loop.is_closed():
            self.loop.call_soon_threadsafe(self._stopping.set)
        if self.thread and threading.current_thread() is not self.thread:
            self.thread.join(timeout=self.config.shutdown_timeout)
            if self.thread.is_alive():
                raise AgentUnavailable("O runtime ainda está encerrando operações em andamento.")

    def call(self, operation, timeout=None):
        """operation é uma fábrica: não criar corrotinas em um loop encerrado."""
        if not self.running or not self.loop or self.loop.is_closed() or not self.server.is_alive():
            raise AgentUnavailable("Runtime SPADE indisponível.")
        if threading.current_thread() is self.thread:
            raise AgentUnavailable("A ponte síncrona não pode bloquear o loop SPADE.")
        future = asyncio.run_coroutine_threadsafe(operation(), self.loop)
        try:
            return future.result(timeout=timeout or self.config.request_timeout)
        except concurrent.futures.TimeoutError:
            future.cancel()
            raise AgentTimeout("A operação não respondeu dentro do prazo.") from None

    async def request(self, source, target, ontology, payload):
        if source not in self.agents or target not in self.agents:
            raise AgentUnavailable("Agente desconhecido.")
        if not self.server.is_alive() or not self.agents[target].is_alive():
            raise AgentUnavailable("Agente de destino indisponível.")
        return await self.agents[source].request(target, ontology, payload, self.config.request_timeout)

    async def _health(self):
        return {
            "running": self.running and self.server.is_alive(),
            "agents": {name: agent.is_alive() and agent.client.is_connected()
                       for name, agent in self.agents.items()},
            "transport": "xmpp",
        }

    def health(self):
        if not self.running or not self.server.is_alive():
            return {"running": False, "agents": {name: False for name in JIDS}, "transport": "xmpp"}
        return self.call(self._health)

    async def _audit(self, message, status):
        # Persistir somente metadados; nunca texto de cliente ou atributos reais.
        def save():
            from app.database import db
            from app.models.log_mensagem_agente import LogMensagemAgente
            with self.app.app_context():
                db.session.add(LogMensagemAgente(
                    sender=str(message.sender.bare) if message.sender else "unknown",
                    receiver=str(message.to.bare) if message.to else "unknown",
                    performative=message.get_metadata("performative")
                        if message.get_metadata("performative") in {"request", "inform", "failure"} else "invalid",
                    ontology=message.get_metadata("ontology")
                        if message.get_metadata("ontology") in ONTOLOGIES else "invalid",
                    thread=safe_thread(message.thread),
                    payload={"redacted": True}, status=status,
                ))
                db.session.commit()
        try:
            await asyncio.to_thread(save)
        except Exception:
            self.app.logger.warning("Não foi possível persistir metadados de comunicação.")


def safe_thread(value):
    from uuid import UUID
    try:
        return str(UUID(value))
    except (ValueError, TypeError, AttributeError):
        return "invalid"
