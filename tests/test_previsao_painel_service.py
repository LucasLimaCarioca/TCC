"""Painel somente leitura com fontes integralmente fictícias."""

from copy import deepcopy
from datetime import date
from decimal import Decimal
import json
from pathlib import Path

import pytest
from sqlalchemy import event

from app.agents.protocol import PREVISAO_PAINEL, decode, encode
from app.database import db
from app.services import previsao_painel_service as painel_module
from app.services.previsao_service import PrevisaoError
from test_previsao_service import configured, project


@pytest.fixture
def published(configured, monkeypatch):
    service, paths, ids = configured
    service.carregar(paths["manifest"])
    monkeypatch.setattr(painel_module, "hoje_manaus", lambda: date(2026, 1, 11))
    return service, paths, ids


def test_catalogo_usa_codigos_do_historico_e_apenas_associacao_explicita(published):
    service, _, ids = published
    panel = service.painel()
    assert [product["codigo_produto"] for product in panel["produtos"]] == ["00110", "00220"]
    mapped, historical = panel["produtos"]
    assert mapped == {"codigo_produto": "00110", "produto_id": ids[0],
        "nome": "Produto Fictício Mapeado", "codigo_grupo": "00005", "categoria": "Grupo Fictício"}
    assert historical["nome"] is historical["produto_id"] is None
    assert historical["codigo_grupo"] == "00005"
    assert panel["modelos"] == {"seasonal_naive": 2}
    assert all(row["fallback"] and row["validacao"] is None and row["teste"] is None
               and row["baselines"] == {"naive": None, "seasonal_naive": None}
               and row["melhor_que_baselines"] is None for row in panel["avaliacoes"])
    assert all(row["treino_inicio"] == "2026-01-02" and row["treino_fim"] == "2026-01-10"
               for row in panel["avaliacoes"])
    assert decode(encode("estoque@localhost", PREVISAO_PAINEL, panel, "inform")).payload == panel


@pytest.mark.parametrize("frequency", ["diaria", "semanal", "mensal"])
def test_recorte_diario_e_somado_antes_das_agregacoes(published, frequency):
    service, _, _ = published
    daily = service.painel(data_inicio="2026-01-13", data_fim="2026-01-16")
    response = service.painel(granularidade=frequency, data_inicio="2026-01-13", data_fim="2026-01-16")
    assert response["total_previsto"] == daily["total_previsto"]
    assert sum(Decimal(row["quantidade_prevista"]) for row in response["periodos"]) == Decimal(daily["total_previsto"])
    assert response["selecao"] == {"codigo_produto": None, "granularidade": frequency,
                                   "data_inicio": "2026-01-13", "data_fim": "2026-01-16"}
    assert response["cobertura"]["inicio"] == "2026-01-11" and response["cobertura"]["fim"] == "2026-01-20"
    assert decode(encode("estoque@localhost", PREVISAO_PAINEL, response, "inform")).payload == response


def test_geral_corresponde_a_soma_dos_produtos_sem_media_de_metricas(published):
    service, _, _ = published
    general = service.painel()
    selected = [service.painel(code) for code in ("00110", "00220")]
    assert Decimal(general["total_previsto"]) == sum(Decimal(item["total_previsto"]) for item in selected)
    for response in selected:
        assert len(response["produtos"]) == 2
        assert len(response["avaliacoes"]) == 1 and sum(response["modelos"].values()) == 1
        assert response["avaliacoes"][0]["codigo_produto"] == response["selecao"]["codigo_produto"]
    assert "metricas" not in general


def test_get_nao_escreve_nem_le_csv_ou_pickle_e_le_snapshot_uma_vez(published, monkeypatch):
    service, _, _ = published
    writes, calls = [], []
    with service.app.app_context():
        engine = db.engine
    def statements(connection, cursor, statement, parameters, context, executemany):
        if statement.lstrip().split()[0].upper() in {"INSERT", "UPDATE", "DELETE", "CREATE", "ALTER", "DROP"}:
            writes.append(statement)
    event.listen(engine, "before_cursor_execute", statements)
    original_snapshot = painel_module.repository.carregar_ativa
    def snapshot():
        calls.append(True)
        return original_snapshot()
    monkeypatch.setattr(painel_module.repository, "carregar_ativa", snapshot)
    original = Path.read_bytes
    def permitted(path):
        assert path.suffix not in {"csv", ".csv", ".pkl", ".pickle"}
        return original(path)
    monkeypatch.setattr(Path, "read_bytes", permitted)
    monkeypatch.setattr("pickle.load", lambda *args: pytest.fail("Não carregar modelos."))
    monkeypatch.setattr("pickle.loads", lambda *args: pytest.fail("Não carregar modelos."))
    try:
        response = service.painel()
    finally:
        event.remove(engine, "before_cursor_execute", statements)
    assert writes == [] and calls == [True]
    encoded = json.dumps(response)
    assert "manifest" not in encoded and "data/" not in encoded and "sha256" not in encoded


@pytest.mark.parametrize("kwargs,code", [
    ({"codigo_produto": "99999"}, "HISTORY_NOT_FOUND"),
    ({"codigo_produto": " 00110"}, "INVALID_INPUT"),
    ({"codigo_produto": "001 10"}, "INVALID_INPUT"),
    ({"codigo_produto": "00110\n"}, "INVALID_INPUT"),
    ({"codigo_produto": "x" * 65}, "INVALID_INPUT"),
    ({"codigo_produto": 110}, "INVALID_INPUT"),
    ({"granularidade": "anual"}, "INVALID_INPUT"),
    ({"granularidade": []}, "INVALID_INPUT"),
    ({"data_inicio": "2026-1-11"}, "INVALID_INPUT"),
    ({"data_inicio": "2026-01-15", "data_fim": "2026-01-14"}, "INVALID_INPUT"),
    ({"data_inicio": "2026-01-01", "data_fim": "2027-01-02"}, "INVALID_INPUT"),
    ({"data_inicio": "2026-01-10"}, "HORIZON_UNAVAILABLE"),
    ({"data_fim": "2026-01-21"}, "HORIZON_UNAVAILABLE"),
])
def test_parametros_rejeitados_sem_recorte_silencioso(published, kwargs, code):
    service, _, _ = published
    with pytest.raises(PrevisaoError) as error:
        service.painel(**kwargs)
    assert error.value.code == code


def test_sem_modelo_e_artefato_divergente_falham_seguro(configured, monkeypatch):
    service, paths, _ = configured
    with pytest.raises(PrevisaoError) as error:
        service.painel()
    assert error.value.code == "MODEL_UNAVAILABLE"
    service.carregar(paths["manifest"])
    original = service._bundle
    def changed(*args, **kwargs):
        path, digest, result, report = original(*args, **kwargs)
        result["generated_at"] = "2026-01-12T00:00:00+00:00"
        return path, digest, result, report
    monkeypatch.setattr(service, "_bundle", changed)
    with pytest.raises(PrevisaoError) as error:
        service.painel()
    assert error.value.code == "INVALID_ARTIFACT"


def test_desatualizada_e_indicacao_apenas_sem_alterar_cobertura(published, monkeypatch):
    service, _, _ = published
    original = service.painel()
    assert original["cobertura"]["desatualizada"] is False
    assert original["atualizacao"] == {"disponivel": True, "data_corte_maxima": "2026-01-10",
        "data_corte_sugerida": "2026-01-10", "horizonte_dias": 10}
    monkeypatch.setattr(painel_module, "hoje_manaus", lambda: date(2026, 1, 15))
    stale = service.painel()
    assert stale["cobertura"]["desatualizada"] is True
    assert stale["periodos"] == original["periodos"]
    assert stale["atualizacao"]["data_corte_maxima"] == "2026-01-10"


@pytest.mark.parametrize("invalid", ["", "missing", "malformed", "incomplete", "wrong_status"])
def test_revisao_ausente_ou_malformada_so_desabilita_atualizacao(published, invalid):
    service, _, _ = published
    if invalid in {"", "missing"}:
        service.app.config["FORECAST_REVIEW"] = invalid
    else:
        review = Path(service.app.config["FORECAST_REVIEW"])
        review.write_text("invalid" if invalid == "malformed" else json.dumps(
            {"status": "aprovado"} if invalid == "wrong_status" else {"status": "revisado_com_recorte"}))
    response = service.painel()
    assert response["atualizacao"] == {"disponivel": False, "data_corte_maxima": None,
        "data_corte_sugerida": None, "horizonte_dias": 10}


def test_avaliacoes_preservam_metricas_individuais_sem_forjar_desempenho(published, monkeypatch):
    service, _, _ = published
    original = service._bundle
    metric = {"mae": 2.0, "rmse": 3.0, "wape": None, "smape": 10.0}
    baseline = {**metric, "mae": 4.0}
    def evaluated(*args, **kwargs):
        path, digest, result, report = original(*args, **kwargs)
        evaluation = report["evaluations"]["00110"]
        evaluation.update(fallback=False, test={"start": "2026-01-05", "end": "2026-01-10",
            "models": {"seasonal_naive": deepcopy(metric), "naive": deepcopy(baseline)}},
            test_comparison={"strictly_better_than_both_baselines": False})
        evaluation["validation"]["candidates"]["seasonal_naive"]["metrics"] = deepcopy(metric)
        return path, digest, result, report
    monkeypatch.setattr(service, "_bundle", evaluated)
    response = service.painel("00110")
    evaluation = response["avaliacoes"][0]
    assert evaluation["validacao"] == evaluation["teste"] == metric
    assert evaluation["baselines"] == {"seasonal_naive": metric, "naive": baseline}
    assert evaluation["melhor_que_baselines"] is False
    decode(encode("estoque@localhost", PREVISAO_PAINEL, response, "inform"))
