"""RPC correlacionado por thread, exclusivamente pelo transporte XMPP local."""

import asyncio

from spade.agent import Agent
from spade.behaviour import CyclicBehaviour
from spade.trace import TraceStore

from app.agents.protocol import JIDS, OPERATIONS, PEERS, PING, ProtocolError, decode, encode, failure


class AgentUnavailable(RuntimeError):
    pass


class AgentTimeout(AgentUnavailable):
    pass


class RemoteFailure(RuntimeError):
    def __init__(self, payload):
        self.code = payload["code"]
        super().__init__(payload["message"])


class XMPPTransport:
    async def send(self, message, behaviour):
        # SPADE Container.send faria entrega direta aos agentes no mesmo processo.
        await behaviour._xmpp_send(message)


class BaseAgent(Agent):
    def __init__(self, role, password, port, audit=None):
        super().__init__(JIDS[role], password, port=port)
        self.role = role
        self.audit = audit
        self.pending = {}
        self.received = 0
        self.traces = TraceStore(size=0)
        self.set_container(XMPPTransport())

    async def setup(self):
        self.inbox = self.Inbox()
        self.add_behaviour(self.inbox)

    class Inbox(CyclicBehaviour):
        async def run(self):
            message = await self.receive(timeout=0.2)
            if message is not None:
                await self.agent.consume(message)

    async def send_message(self, message):
        await self.inbox.send(message)
        if self.audit:
            await self.audit(message, "sent")

    async def request(self, target, ontology, payload, timeout):
        if not self.is_alive() or not self.client.is_connected():
            raise AgentUnavailable("Agente desconectado.")
        if target not in PEERS[self.role]:
            raise ProtocolError("Comunicação fora da topologia permitida.")
        message = encode(JIDS[target], ontology, payload)
        future = asyncio.get_running_loop().create_future()
        self.pending[message.thread] = (JIDS[target], ontology, future)
        try:
            await self.send_message(message)
            response = await asyncio.wait_for(future, timeout=timeout)
            if response.performative == "failure":
                raise RemoteFailure(response.payload)
            return response
        except asyncio.TimeoutError:
            raise AgentTimeout("O agente não respondeu dentro do prazo.") from None
        finally:
            self.pending.pop(message.thread, None)

    async def consume(self, message):
        sender = message.sender.bare if message.sender else ""
        if sender not in {JIDS[name] for name in PEERS[self.role]}:
            return
        self.received += 1
        if self.audit:
            await self.audit(message, "received")
        try:
            envelope = decode(message)
        except ProtocolError:
            # Sem thread/ontology não existe correlação segura para uma resposta.
            if (message.get_metadata("performative") == "request" and
                    message.thread and message.get_metadata("ontology")):
                await self.send_message(failure(sender, message.get_metadata("ontology"),
                                                message.thread, "INVALID_MESSAGE",
                                                "Mensagem fora do contrato."))
            return
        if envelope.performative != "request":
            pending = self.pending.get(envelope.thread)
            if pending and pending[:2] == (sender, envelope.ontology):
                if not pending[2].done():
                    pending[2].set_result(envelope)
            return
        if envelope.ontology not in OPERATIONS[self.role]:
            reply = failure(sender, envelope.ontology, envelope.thread, "UNSUPPORTED_ONTOLOGY",
                            "Operação não pertence a este agente.")
        elif envelope.ontology == PING:
            reply = encode(sender, PING, {"agent": self.role, "status": "ok"},
                           "inform", envelope.thread)
        else:
            reply = failure(sender, envelope.ontology, envelope.thread, "NOT_IMPLEMENTED",
                            "Operação prevista para uma fase posterior.")
        await self.send_message(reply)

    async def stop(self):
        for _, _, future in self.pending.values():
            if not future.done():
                future.set_exception(AgentUnavailable("Agente encerrado."))
        await super().stop()
        if self.client and not self.is_alive() and self.client.is_connected():
            await self.client.disconnect()
