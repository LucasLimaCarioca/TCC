"""Snapshot somente leitura para o painel, sem desserializar modelos ou ler CSVs."""

from collections import Counter
from datetime import timedelta
from decimal import Decimal, localcontext

from sqlalchemy import select

from app.database import db
from app.forecasting.service import OUTPUT_FIELDS
from app.models.produto import Produto
from app.services import previsao_repository as repository
from app.services.previsao_service import PrevisaoError, _day, _periods, _text, hoje_manaus


FREQUENCIES = {"diaria", "semanal", "mensal"}


def _code(value):
    return (type(value) is str and 1 <= len(value) <= 64
            and not any(char.isspace() or ord(char) < 32 or ord(char) == 127 for char in value))


def _optional_text(value, limit):
    if value is not None and (type(value) is not str or not 0 < len(value) <= limit
                             or any(ord(char) < 32 or ord(char) == 127 for char in value)):
        raise PrevisaoError("INVALID_ARTIFACT", "Os metadados da previsão são inválidos.")
    return value


def _update(service, result, today):
    disabled = {"disponivel": False, "data_corte_maxima": None,
                "data_corte_sugerida": None, "horizonte_dias": result["horizon_days"]}
    configured = service._config("FORECAST_REVIEW")
    if not configured:
        return disabled
    try:
        path = service._file(configured, "reports")
        review = service._json(path.read_bytes())
        if (type(review) is not dict or review.get("status") != "revisado_com_recorte"
                or review.get("politica_data") != "dia_fonte"
                or review.get("colunas") != list(OUTPUT_FIELDS)
                or type(review.get("contexto")) is not dict
                or review["contexto"].get("exportacao_completa") is not True):
            return disabled
        maximum = min(_day(review.get("data_referencia")), today - timedelta(days=1))
        cutoff = _day(result["cutoff"])
        return {"disponivel": True, "data_corte_maxima": maximum.isoformat(),
                "data_corte_sugerida": cutoff.isoformat() if cutoff <= maximum else None,
                "horizonte_dias": result["horizon_days"]}
    except Exception:
        # A indicação não substitui a validação completa feita pelo worker.
        return disabled


def painel(service, codigo_produto=None, granularidade="diaria", data_inicio=None, data_fim=None):
    """Recorta dias da publicação única e agrega depois, sem escrever no banco."""
    if ((codigo_produto is not None and not _code(codigo_produto))
            or type(granularidade) is not str or granularidade not in FREQUENCIES):
        raise PrevisaoError("INVALID_INPUT", "Parâmetros do painel de previsão inválidos.")
    start = _day(data_inicio) if data_inicio is not None else None
    end = _day(data_fim) if data_fim is not None else None
    if start is not None and end is not None and (start > end or (end - start).days >= 366):
        raise PrevisaoError("INVALID_INPUT", "O intervalo deve ser ordenado e conter até 366 dias.")
    try:
        with service.app.app_context():
            active = repository.carregar_ativa()
            if active is None:
                raise PrevisaoError("MODEL_UNAVAILABLE", "Nenhuma execução de previsão foi ativada.")
            catalog = {code: {"produto_id": identifier, "nome": name}
                       for code, identifier, name in db.session.execute(select(
                           Produto.codigo_externo, Produto.id, Produto.nome
                       ).where(Produto.codigo_externo.is_not(None))).all()}
        _, _, result, report = service._bundle(
            active["manifest_path"], active["manifest_sha256"], check_all=False)
        if result != active["result"]:
            raise PrevisaoError("INVALID_ARTIFACT", "A previsão diverge da execução persistida.")
        codes = sorted(result["forecast"]["products"])
        if codigo_produto is not None and codigo_produto not in codes:
            raise PrevisaoError("HISTORY_NOT_FOUND", "O produto não possui histórico elegível nesta execução.")
        cutoff = _day(result["cutoff"])
        coverage_start, coverage_end = cutoff + timedelta(days=1), cutoff + timedelta(days=result["horizon_days"])
        start, end = start or coverage_start, end or coverage_end
        if start > end or (end - start).days >= 366:
            raise PrevisaoError("INVALID_INPUT", "O intervalo deve ser ordenado e conter até 366 dias.")
        if start < coverage_start or end > coverage_end:
            raise PrevisaoError("HORIZON_UNAVAILABLE", "O intervalo solicitado excede a cobertura da previsão ativa.")
        selected = codes if codigo_produto is None else [codigo_produto]
        products, evaluations = [], []
        metadata = report["dataset"]["products"]
        for code in codes:
            if not _code(code):
                raise PrevisaoError("INVALID_ARTIFACT", "Os metadados da previsão são inválidos.")
            item = metadata[code]
            mapped = catalog.get(code, {"produto_id": None, "nome": None})
            products.append({"codigo_produto": code, **mapped,
                "codigo_grupo": _optional_text(item.get("codigo_grupo"), 64),
                "categoria": _optional_text(item.get("categoria"), 100)})
            if code not in selected:
                continue
            evaluation = report["evaluations"][code]
            model, test = result["models"][code], evaluation["test"]
            training_start = _day(item["first_observation"])
            if training_start > cutoff:
                raise PrevisaoError("INVALID_ARTIFACT", "Os metadados da previsão são inválidos.")
            evaluations.append({"codigo_produto": code, "modelo": model,
                "fallback": evaluation["fallback"], "motivo": evaluation["selection_reason"],
                "treino_inicio": training_start.isoformat(), "treino_fim": result["cutoff"],
                "validacao": evaluation["validation"]["candidates"][model]["metrics"],
                "teste_inicio": None if test is None else test["start"],
                "teste_fim": None if test is None else test["end"],
                "teste": None if test is None else test["models"][model],
                "baselines": {name: None if test is None else test["models"][name]
                              for name in ("naive", "seasonal_naive")},
                "melhor_que_baselines": None if test is None else evaluation[
                    "test_comparison"]["strictly_better_than_both_baselines"]})
        view = result["forecast"]["overall"] if codigo_produto is None else result["forecast"]["products"][codigo_produto]
        daily = view["diaria"][(start - coverage_start).days:(end - coverage_start).days + 1]
        days = [_day(row["periodo_inicio"]) for row in daily]
        values = [Decimal(row["quantidade_prevista"]) for row in daily]
        today = hoje_manaus()
        with localcontext() as context:
            context.prec = 400
            return {"execucao_id": result["run_id"], "versao": result["version"], "produtos": products,
                "selecao": {"codigo_produto": codigo_produto, "granularidade": granularidade,
                            "data_inicio": start.isoformat(), "data_fim": end.isoformat()},
                "cobertura": {"inicio": coverage_start.isoformat(), "fim": coverage_end.isoformat(),
                    "data_corte": result["cutoff"], "gerada_em": result["generated_at"],
                    "desatualizada": cutoff != today - timedelta(days=1)},
                "total_previsto": _text(sum(values, Decimal(0))),
                "periodos": _periods(days, values, granularidade),
                "modelos": dict(sorted(Counter(result["models"][code] for code in selected).items())),
                "avaliacoes": evaluations, "atualizacao": _update(service, result, today)}
    except PrevisaoError:
        raise
    except repository.RepositoryError as error:
        raise PrevisaoError(error.code, str(error)) from None
    except (KeyError, TypeError, ValueError, ArithmeticError):
        raise PrevisaoError("INVALID_ARTIFACT", "Os metadados da previsão são inválidos.") from None
    except Exception:
        raise PrevisaoError("STORAGE_ERROR", "Não foi possível consultar o painel de previsão.") from None
