"""Configuração dos alertas sem herdar o ambiente real nos testes."""

import os

from app.services.estoque_service import EstoqueError


def _value(app, key, default):
    return app.config.get(key, default if app.testing else os.environ.get(key, default))


def horizonte_preventivo(app, valor=None):
    value = _value(app, "PREDICTIVE_ALERT_HORIZON_DAYS", 7) if valor is None else valor
    if valor is None and type(value) is str and value.isascii() and value.isdecimal():
        value = int(value)
    if type(value) is not int or not 1 <= value <= 366:
        raise EstoqueError("INVALID_INPUT", "O horizonte do alerta deve estar entre 1 e 366 dias.")
    return value


def horizonte_persistente(app, valor=None):
    """Somente o horizonte oficial pode alterar alertas persistentes."""
    official = horizonte_preventivo(app)
    if valor is not None and horizonte_preventivo(app, valor) != official:
        raise EstoqueError("ALERT_HORIZON_MISMATCH",
            "Alertas persistentes usam o horizonte configurado em PREDICTIVE_ALERT_HORIZON_DAYS. "
            "Para outros horizontes, consulte o risco sem atualizar o alerta.")
    return official


def automatico_ativo(app):
    value = _value(app, "PREDICTIVE_ALERTS_AUTO_ENABLED", not app.testing)
    if type(value) is str and value in {"0", "1"}:
        return value == "1"
    if type(value) is not bool:
        raise EstoqueError("INVALID_INPUT", "Configure a atualização automática de alertas com 0 ou 1.")
    return value
