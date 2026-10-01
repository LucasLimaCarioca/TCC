"""Configuração local. Senhas vêm do ambiente/configuração e não aparecem no repr."""

import math
import os
from dataclasses import dataclass, field

from app.agents.protocol import JIDS


@dataclass(frozen=True)
class RuntimeConfig:
    passwords: dict = field(repr=False)
    client_port: int = 5222
    server_port: int = 5269
    startup_timeout: float = 20.0
    request_timeout: float = 5.0
    shutdown_timeout: float = 10.0

    def __post_init__(self):
        if set(self.passwords) != set(JIDS) or not all(
            isinstance(value, str) and value.strip() for value in self.passwords.values()
        ):
            raise ValueError("Configure as três senhas XMPP dos agentes.")
        if not all(type(port) is int and 1 <= port <= 65535
                   for port in (self.client_port, self.server_port)) or self.client_port == self.server_port:
            raise ValueError("Configure portas XMPP distintas entre 1 e 65535.")
        if not all(type(value) in (int, float) and math.isfinite(value) and value > 0
                   for value in (self.startup_timeout, self.request_timeout, self.shutdown_timeout)):
            raise ValueError("Os timeouts devem ser positivos e finitos.")

    @classmethod
    def from_mapping(cls, config):
        def value(key, default=None):
            return config.get(key, os.environ.get(key, default))
        return cls(
            passwords={name: value(f"XMPP_{name.upper()}_PASSWORD") for name in JIDS},
            client_port=int(value("XMPP_CLIENT_PORT", 5222)),
            server_port=int(value("XMPP_SERVER_PORT", 5269)),
            startup_timeout=float(value("AGENT_STARTUP_TIMEOUT", 20)),
            request_timeout=float(value("AGENT_REQUEST_TIMEOUT", 5)),
            shutdown_timeout=float(value("AGENT_SHUTDOWN_TIMEOUT", 10)),
        )
