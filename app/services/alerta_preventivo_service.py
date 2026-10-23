"""Reconcilia risco futuro com uma previsão diária confirmada após o RPC.

O snapshot de cada avaliação tem ``previsao`` (o payload F7 integral),
``disponivel``, ``estoque_minimo``, ``saldo_projetado`` (decimal canônico) e
``avaliada_em`` (ISO UTC). A previsão nunca movimenta ou reserva estoque.
"""

from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, localcontext
from zoneinfo import ZoneInfo

from flask import current_app
from sqlalchemy import select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.agents.protocol import PREVISAO, ProtocolError, validate_payload
from app.database import db
from app.models.alerta_estoque import AlertaEstoque
from app.models.previsao_execucao import PrevisaoExecucao
from app.models.produto import Produto
from app.models.timestamps import utc_now
from app.services import previsao_repository as repository
from app.services.estoque_service import EstoqueError, inteiro
from app.services.alerta_preventivo_config import horizonte_persistente


def _hoje(value):
    if value is None:
        return datetime.now(ZoneInfo("America/Manaus")).date()
    if type(value) is not date:
        raise EstoqueError("INVALID_INPUT", "Informe uma data comercial válida.")
    return value


def _nao_cancelado(cancel_event):
    if cancel_event is not None:
        if not callable(getattr(cancel_event, "is_set", None)):
            raise EstoqueError("INVALID_INPUT", "O controle de cancelamento da avaliação é inválido.")
        if cancel_event.is_set():
            raise EstoqueError("FORECAST_CANCELLED", "A avaliação preventiva foi cancelada durante o encerramento.")


def _decimal(value):
    if value == 0:
        return "0"
    formatted = format(value, "f")
    return formatted.rstrip("0").rstrip(".") if "." in formatted else formatted


def _payload(value):
    try:
        copied = deepcopy(value)
        validate_payload(PREVISAO, "inform", copied)
        if copied["produto_id"] is None or copied["granularidade"] != "diaria":
            raise ProtocolError("Mensagem fora do contrato.")
        return copied
    except (ProtocolError, ValueError, TypeError, KeyError, AttributeError, ArithmeticError):
        raise EstoqueError("INVALID_RESPONSE", "A resposta de previsão não permite avaliar o risco do produto.") from None


def _vigencia(payload, horizon, today):
    # Exige o dia comercial completo anterior: não desliza uma previsão antiga
    # nem transforma sua falta de cobertura em demanda igual a zero.
    if (payload["data_corte"] != (today - timedelta(days=1)).isoformat()
            or payload["cobertura_inicio"] != today.isoformat()
            or payload["cobertura_fim"] != (today + timedelta(days=horizon - 1)).isoformat()):
        raise EstoqueError("FORECAST_STALE", "A previsão não cobre o horizonte atual a partir de hoje em Manaus.")


def _confirmar_execucao(session, product, payload, horizon):
    execution = session.scalars(select(PrevisaoExecucao).where(
        PrevisaoExecucao.ativa.is_(True))).one_or_none()
    if execution is None or execution.id != payload["execucao_id"]:
        raise EstoqueError("FORECAST_CHANGED", "A execução de previsão mudou; consulte novamente antes de avaliar o risco.")
    if product.codigo_externo != payload["codigo_produto"]:
        raise EstoqueError("FORECAST_CHANGED", "A associação do produto mudou; consulte novamente a previsão.")
    try:
        result = execution.resultado_json
        report = {**execution.metadados_json, "evaluations": execution.metricas_json}
        repository.validar_execucao(result, report)
        generated = datetime.fromisoformat(result["generated_at"]).astimezone(timezone.utc).replace(tzinfo=None)
        expected = (result["run_id"], result["version"], date.fromisoformat(result["cutoff"]),
                    generated, result["horizon_days"])
        actual = (execution.id, execution.versao_motor, execution.data_corte,
                  execution.gerada_em, execution.horizonte_dias)
        code = product.codigo_externo
        if (actual != expected or horizon > execution.horizonte_dias
                or payload["data_corte"] != result["cutoff"]
                or payload["gerada_em"] != result["generated_at"]
                or payload["versao"] != result["version"]
                or payload["modelo"] != result["models"][code]
                or payload["periodos"] != result["forecast"]["products"][code]["diaria"][:horizon]):
            raise ValueError
    except (repository.RepositoryError, ValueError, TypeError, KeyError, AttributeError, ArithmeticError):
        raise EstoqueError("FORECAST_CHANGED", "A resposta diverge da execução ativa; consulte novamente a previsão.") from None


def sincronizar_alerta_preventivo(produto_id, previsao, horizonte_dias=None, *, hoje=None, cancel_event=None):
    """Cria, atualiza ou resolve somente o risco futuro do produto informado.

    O chamador obtém a previsão via RPC antes desta função. Esta sessão reserva
    a escrita antes de reler estoque, vínculo e execução ativa; nenhum estado
    observado antes do RPC é usado para decidir ou resolver um alerta.
    """
    _nao_cancelado(cancel_event)
    product_id, horizon, today = inteiro(produto_id), horizonte_persistente(current_app, horizonte_dias), _hoje(hoje)
    payload = _payload(previsao)
    if payload["produto_id"] != product_id or payload["horizonte_dias"] != horizon:
        raise EstoqueError("INVALID_RESPONSE", "A previsão recebida não corresponde ao produto e horizonte solicitados.")
    _vigencia(payload, horizon, today)
    try:
        with Session(db.engine, expire_on_commit=False) as session:
            try:
                session.execute(text("BEGIN IMMEDIATE"))
                _nao_cancelado(cancel_event)
                horizonte_persistente(current_app, horizon)
                product = session.get(Produto, product_id)
                if product is None or not product.ativo:
                    raise EstoqueError("PRODUCT_NOT_FOUND", "Produto ativo não encontrado no catálogo.")
                if not product.codigo_externo:
                    raise EstoqueError("PRODUCT_UNMAPPED", "O produto não possui código externo associado ao histórico.")
                _confirmar_execucao(session, product, payload, horizon)
                with localcontext() as context:
                    context.prec = 450
                    demand = sum((Decimal(period["quantidade_prevista"]) for period in payload["periodos"]), Decimal(0))
                    projected = Decimal(product.quantidade_disponivel) - demand
                    risk = projected < product.estoque_minimo
                    projected_text = _decimal(projected)
                now = utc_now()
                snapshot = {"previsao": payload, "disponivel": product.quantidade_disponivel,
                            "estoque_minimo": product.estoque_minimo, "saldo_projetado": projected_text,
                            "avaliada_em": now.replace(tzinfo=timezone.utc).isoformat()}
                alert = session.scalars(select(AlertaEstoque).where(
                    AlertaEstoque.tipo_item == "produto", AlertaEstoque.produto_id == product_id,
                    AlertaEstoque.tipo_alerta == "risco_futuro", AlertaEstoque.ativo.is_(True))).one_or_none()
                action = "sem_alerta"
                if risk and alert is None:
                    alert = AlertaEstoque(tipo_item="produto", produto_id=product_id,
                        tipo_alerta="risco_futuro", nivel="aviso", ativo=True)
                    session.add(alert)
                    action = "criado"
                elif risk:
                    action = "atualizado"
                elif alert is not None:
                    alert.ativo = False
                    alert.resolvido_em = now
                    action = "resolvido"
                if alert is not None:
                    prefix = "Risco futuro" if risk else "Risco futuro resolvido"
                    alert.mensagem = (f"{prefix} de {product.nome}: saldo atual {product.quantidade_disponivel}, "
                        f"demanda prevista {payload['total_previsto']} em {horizon} dias "
                        f"({payload['cobertura_inicio']} a {payload['cobertura_fim']}), "
                        f"saldo projetado {projected_text}, mínimo {product.estoque_minimo}.")
                    alert.previsao_json = snapshot
                    alert.atualizado_em = now
                    session.flush()
                result = {"produto_id": product_id, "status": "ok", "risco": risk,
                          "saldo_projetado": projected_text, "disponivel": product.quantidade_disponivel,
                          "estoque_minimo": product.estoque_minimo, "alerta_id": alert.id if alert else None,
                          "acao": action, "previsao": payload}
                _nao_cancelado(cancel_event)
                horizonte_persistente(current_app, horizon)
                session.commit()
            except BaseException:
                session.rollback()
                raise
        db.session.expire_all()
        return result
    except SQLAlchemyError:
        raise EstoqueError("STORAGE_ERROR", "Não foi possível registrar a avaliação preventiva de estoque.") from None


def estado_alerta_preventivo(alerta, produto, execucao_ativa_id, *, hoje=None):
    """Informa a validade do alerta sem consultar ou alterar o banco de dados."""
    def state(reason):
        return {"atual": reason == "atual", "motivo": reason}

    if not alerta.ativo:
        return state("resolvido")
    snapshot = alerta.previsao_json
    if snapshot is None:
        return state("legado_sem_previsao")
    try:
        if type(snapshot) is not dict or set(snapshot) != {
            "previsao", "disponivel", "estoque_minimo", "saldo_projetado", "avaliada_em"
        }:
            raise ValueError
        payload = _payload(snapshot["previsao"])
        evaluated = datetime.fromisoformat(snapshot["avaliada_em"])
        if evaluated.utcoffset() != timedelta(0):
            raise ValueError
        if (type(snapshot["disponivel"]) is not int or snapshot["disponivel"] < 0
                or type(snapshot["estoque_minimo"]) is not int or snapshot["estoque_minimo"] < 0
                or type(snapshot["saldo_projetado"]) is not str
                or alerta.tipo_item != "produto" or alerta.tipo_alerta != "risco_futuro"
                or payload["produto_id"] != alerta.produto_id):
            raise ValueError
        with localcontext() as context:
            context.prec = 450
            projected = Decimal(snapshot["disponivel"]) - Decimal(payload["total_previsto"])
            if (_decimal(projected) != snapshot["saldo_projetado"] or projected >= snapshot["estoque_minimo"]):
                raise ValueError
        today = _hoje(hoje)
    except (EstoqueError, ValueError, TypeError, KeyError, AttributeError, ArithmeticError):
        return state("snapshot_invalido")
    if produto is None or not produto.ativo or produto.id != alerta.produto_id:
        return state("produto_inativo")
    if produto.codigo_externo != payload["codigo_produto"]:
        return state("associacao_alterada")
    if produto.quantidade_disponivel != snapshot["disponivel"]:
        return state("estoque_alterado")
    if produto.estoque_minimo != snapshot["estoque_minimo"]:
        return state("minimo_alterado")
    if execucao_ativa_id != payload["execucao_id"]:
        return state("execucao_alterada")
    if payload["horizonte_dias"] != horizonte_persistente(current_app):
        return state("horizonte_alterado")
    try:
        _vigencia(payload, payload["horizonte_dias"], today)
    except EstoqueError:
        return state("previsao_vencida")
    return state("atual")
