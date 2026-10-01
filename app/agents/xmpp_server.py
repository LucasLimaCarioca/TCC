"""Servidor embutido PyJabber (o mesmo do SPADE), isolado e gerenciado pelo runtime.

O processo próprio permite usar os handlers de sinais do PyJabber na thread
principal e evita compartilhar seus singletons entre event loops/reinicializações.
"""

import asyncio
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path


def _create_certificates(directory):
    # O gerador PyJabber exige uma CA pré-existente. Usar certificado efêmero
    # próprio de localhost evita copiar chaves da dependência para o projeto.
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
    now = datetime.now(timezone.utc)
    certificate = (x509.CertificateBuilder().subject_name(name).issuer_name(name)
                   .public_key(key.public_key()).serial_number(x509.random_serial_number())
                   .not_valid_before(now - timedelta(minutes=1))
                   .not_valid_after(now + timedelta(days=1))
                   .add_extension(x509.SubjectAlternativeName([x509.DNSName("localhost")]), False)
                   .sign(key, hashes.SHA256()))
    files = {
        "localhost_key.pem": key.private_bytes(serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8, serialization.NoEncryption()),
        "localhost_cert.pem": certificate.public_bytes(serialization.Encoding.PEM),
    }
    for filename, content in files.items():
        with os.fdopen(os.open(Path(directory) / filename, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb") as output:
            output.write(content)


def serve(client_port, server_port, cert_path, ready, stopping):
    # Importações dentro do worker: nenhum servidor é criado ao importar o app.
    from loguru import logger
    from pyjabber.server import Server
    from pyjabber.server_parameters import Parameters

    logger.disable("pyjabber")  # Não imprimir stanzas, autenticação ou payloads.

    class LocalServer(Server):
        @staticmethod
        def raise_exit(*args):
            if args:  # SIGTERM/SIGINT: encerrar pelo ciclo de vida normal.
                stopping.set()
                return
            raise RuntimeError("Não foi possível iniciar o servidor XMPP local.")

    async def main():
        _create_certificates(cert_path)
        server = LocalServer(Parameters(
            host="localhost", client_port=client_port, server_port=server_port,
            cert_path=cert_path, database_in_memory=True, message_persistence=False,
            plugins=["jabber:iq:register", "jabber:x:data", "urn:xmpp:ping"],
            items={"pubsub.$": {"name": "Local PubSub", "category": "pubsub",
                                "type": "service", "var": "http://jabber.org/protocol/pubsub",
                                "extra": {}}},
        ))
        # PyJabber também vincula o IP da LAN por padrão. Aqui só há loopback.
        server._public_ip = None
        from concurrent.futures import ThreadPoolExecutor
        from attrs import evolve
        from pyjabber import AppConfig
        # PBKDF2 usa um executor no PyJabber. Limitar a dois workers evita
        # processos filhos por CPU e mantém o servidor gerenciado isolado.
        AppConfig.app_config.process_pool_exe.shutdown(wait=False)
        AppConfig.app_config = evolve(
            AppConfig.app_config, process_pool_exe=ThreadPoolExecutor(max_workers=2)
        )
        # PyJabber 0.4.5 instancia HTTPFieldUpload em todo StanzaHandler,
        # mesmo sem o plugin habilitado. Adaptar só o worker isolado evita
        # abrir HTTP/armazenamento de uploads para a comunicação deste projeto.
        from importlib import import_module
        from pyjabber.plugins.PluginManager import PluginManager
        from pyjabber.plugins.roster.Roster import Roster
        from pyjabber.plugins.xep_0199.xep_0199 import Ping

        class LocalPluginManager(PluginManager):
            def __init__(self, jid):
                self._jid = jid
                self._plugins = {"jabber:iq:roster": Roster(), "urn:xmpp:ping": Ping}

        handler_module = import_module("pyjabber.stream.handlers.StanzaHandler")
        handler_module.PluginManager = LocalPluginManager

        # Em 0.4.5 o parser é resetado APÓS await start_tls. O primeiro header
        # cifrado pode chegar nesse await e ser descartado. Resetar antes da
        # troca de transporte, preservando o header recebido durante o handshake.
        from pyjabber.stream.negotiators.StreamNegotiator import StreamNegotiator
        from pyjabber.features.Features import start_tls_proceed_response
        from pyjabber.stream.utils.Enums import Stage
        from pyjabber.utils.Exceptions import NotAuthorizerStreamNegotiationException

        async def handle_tls(negotiator, element):
            if element.tag != "{urn:ietf:params:xml:ns:xmpp-tls}starttls":
                raise NotAuthorizerStreamNegotiationException()
            transport = negotiator._transport
            transport.write(start_tls_proceed_response())
            transport.pause_reading()
            negotiator._parser.reset_stack()
            negotiator._stage = Stage.SSL
            secured = await asyncio.get_running_loop().start_tls(
                transport, negotiator._protocol, AppConfig.app_config.ssl_context,
                server_side=True,
            )
            if secured is None:
                raise ConnectionError("Negociação TLS interrompida.")
            negotiator._transport = secured
            negotiator._protocol.transport = secured
            negotiator._parser.transport = secured
            negotiator._handler.transport = secured
            negotiator._connection_manager.update_transport_peer(secured, negotiator._peer)
            secured.resume_reading()

        StreamNegotiator._handle_tls = handle_tls
        task = asyncio.create_task(server.start())
        waiter = asyncio.create_task(server.ready.wait())
        try:
            done, _ = await asyncio.wait({task, waiter}, return_when=asyncio.FIRST_COMPLETED)
            if task in done:
                await task
                raise RuntimeError("Servidor XMPP encerrado durante a inicialização.")
            ready.set()
            while not stopping.is_set():
                if task.done():
                    await task
                    break
                await asyncio.sleep(0.05)
        finally:
            waiter.cancel()
            task.cancel()
            await asyncio.gather(task, waiter, return_exceptions=True)
            for listener in (server._client_listener, server._server_listener):
                if listener:
                    listener.close()
                    await listener.wait_closed()
            from pyjabber import AppConfig
            from pyjabber.db.database import DB
            if DB._engine:
                await DB.close_engine_async()
            AppConfig.app_config.process_pool_exe.shutdown(wait=False, cancel_futures=True)

    try:
        asyncio.run(main())
    except (Exception, SystemExit) as error:
        # O runtime detecta o exitcode; conteúdo sensível não entra em traceback.
        print("Falha do servidor XMPP:", type(error).__name__, flush=True)
        raise SystemExit(1) from None
