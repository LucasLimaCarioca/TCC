"""Adaptação do motor real com fontes exclusivamente fictícias."""

from datetime import date
from decimal import Decimal
import json
from threading import Event

import pytest

from app.database import db
from app.models.produto import Produto
from app.services import previsao_service as adapter
from app.services.previsao_service import PrevisaoError, PrevisaoService
from app.services import previsao_repository as repository
from test_forecasting_service import project, execute


@pytest.fixture
def configured(app, project):
    root, review = project
    app.config.update(FORECAST_PROJECT_ROOT=root, FORECAST_REVIEW=str(review), FORECAST_START="2026-01-01")
    paths = execute(project)
    service = PrevisaoService(app)
    with app.app_context():
        product = Produto(nome="Produto Fictício Mapeado", codigo_externo="00110", preco=1,
                          quantidade_disponivel=20, categoria="Formato fictício", sabor="Sabor fictício")
        unmapped = Produto(nome="Produto Fictício Sem Mapeamento", preco=1, quantidade_disponivel=20)
        missing = Produto(nome="Produto Fictício Sem Histórico", codigo_externo="00999", preco=1, quantidade_disponivel=20)
        db.session.add_all([product, unmapped, missing])
        db.session.commit()
        ids = product.id, unmapped.id, missing.id
        # Identidade fictícia explicitamente conferida; dados legados reais não
        # recebem aprovação automática na migração nem na carga de previsões.
        from app.services.catalogacao_service import _review
        report = json.loads(paths["report"].read_text())
        metadata = {code: {"categorias": [item["categoria"]] if item.get("categoria") else [],
                           "grupos": [item["codigo_grupo"]] if item.get("codigo_grupo") else []}
                    for code, item in report["dataset"]["products"].items()}
        _review(db.session, product, metadata, "Operador fictício", "Identidade fictícia conferida no fixture.")
        db.session.commit()
    return service, paths, ids


def test_carregar_persistir_consultar_e_recuperar_apos_reinicio(configured):
    service, paths, (product, _, _) = configured
    response = service.carregar(paths["manifest"])
    assert response["status"] == "concluido"
    service.inicializar()
    restarted = PrevisaoService(service.app)
    restarted.inicializar()
    forecast = restarted.consultar(product, horizonte_dias=7)
    assert forecast["codigo_produto"] == "00110"
    assert forecast["produto_id"] == product
    assert forecast["execucao_id"] == response["run_id"]
    assert forecast["cobertura_inicio"] == "2026-01-11"
    assert forecast["cobertura_fim"] == "2026-01-17"
    assert forecast["horizonte_dias"] == len(forecast["periodos"]) == 7
    assert all(isinstance(period["quantidade_prevista"], str) for period in forecast["periodos"])


def test_horizonte_recorta_diarios_antes_de_agregar_e_geral_soma_produtos(configured):
    service, paths, (product, _, _) = configured
    service.carregar(paths["manifest"])
    daily = service.consultar(product, horizonte_dias=3)
    expected = sum(Decimal(p["quantidade_prevista"]) for p in daily["periodos"])
    assert Decimal(daily["total_previsto"]) == expected
    for frequency in ("semanal", "mensal"):
        response = service.consultar(product, frequency, 3)
        assert Decimal(response["total_previsto"]) == expected
        assert sum(Decimal(p["quantidade_prevista"]) for p in response["periodos"]) == expected
        assert response["cobertura_fim"] == "2026-01-13"
        assert all(p["parcial"] for p in response["periodos"])
    general = service.consultar(None, "mensal", 3)
    raw = json.loads(paths["forecast"].read_text())["forecast"]
    product_sum = sum(Decimal(p["quantidade_prevista"]) for view in raw["products"].values() for p in view["diaria"][:3])
    assert Decimal(general["total_previsto"]) == product_sum
    assert general["produto_id"] is general["codigo_produto"] is None
    assert isinstance(general["modelo"], dict)


def test_sem_execucao_ativa_falha_segura(configured):
    service, _, (product, _, _) = configured
    service.inicializar()  # Não encontra nem ativa automaticamente arquivos do disco.
    with pytest.raises(PrevisaoError) as error:
        service.consultar(product)
    assert error.value.code == "MODEL_UNAVAILABLE"


def test_testes_nao_herdam_artefatos_reais_configurados_no_ambiente(configured, monkeypatch):
    service, _, _ = configured
    monkeypatch.setenv("FORECAST_MANIFEST", "/caminho/ficticio/que/nao/deve/ser/lido")
    monkeypatch.setenv("FORECAST_START", "2021-01-01")
    service.inicializar()
    assert service._config("FORECAST_MANIFEST") is None
    assert service._config("FORECAST_START") == "2026-01-01"


@pytest.mark.parametrize("index,code", [(1, "PRODUCT_UNMAPPED"), (2, "HISTORY_NOT_FOUND")])
def test_associacao_explicita_sem_inferir_por_nomes(configured, index, code):
    service, paths, ids = configured
    service.carregar(paths["manifest"])
    with pytest.raises(PrevisaoError) as error:
        service.consultar(ids[index])
    assert error.value.code == code


@pytest.mark.parametrize("kwargs,code", [({"produto_id": 99999}, "PRODUCT_NOT_FOUND"),
    ({"produto_id": True}, "INVALID_INPUT"), ({"horizonte_dias": 0}, "INVALID_INPUT"),
    ({"horizonte_dias": 367}, "INVALID_INPUT"), ({"horizonte_dias": 11}, "HORIZON_UNAVAILABLE"),
    ({"granularidade": "anual"}, "INVALID_INPUT")])
def test_erros_de_consulta_sao_tipados(configured, kwargs, code):
    service, paths, _ = configured
    service.carregar(paths["manifest"])
    with pytest.raises(PrevisaoError) as error:
        service.consultar(**kwargs)
    assert error.value.code == code


@pytest.mark.parametrize("target", ["forecast", "report", "model", "manifest"])
def test_alteracao_de_artefato_bloqueia_resposta_sem_expor_conteudo(configured, target):
    service, paths, _ = configured
    service.carregar(paths["manifest"])
    if target == "model":
        manifest = json.loads(paths["manifest"].read_text())
        path = service.root / next(iter(manifest["models"].values()))
    else:
        path = paths[target]
    path.write_bytes(b"CONTEUDO_FICTICIO_QUE_NAO_PODE_APARECER")
    with pytest.raises(PrevisaoError) as error:
        if target == "model":
            service.carregar(paths["manifest"])
        else:
            service.consultar()
    assert error.value.code == "INVALID_ARTIFACT"
    assert "CONTEUDO_FICTICIO" not in str(error.value)


def test_manifesto_configurado_e_carregado_sem_ler_pickle(configured, monkeypatch):
    service, paths, _ = configured
    service.app.config["FORECAST_MANIFEST"] = str(paths["manifest"])
    def forbidden(*args, **kwargs):
        pytest.fail("Não deve desserializar pickle para servir previsões JSON.")
    monkeypatch.setattr("pickle.loads", forbidden)
    monkeypatch.setattr("pickle.load", forbidden)
    service.inicializar()
    assert service.consultar()["execucao_id"]


def test_inicializacao_cancelada_nao_publica_manifesto_configurado(configured):
    service, paths, _ = configured
    service.app.config["FORECAST_MANIFEST"] = str(paths["manifest"])
    cancelled = Event()
    cancelled.set()
    with pytest.raises(PrevisaoError) as error:
        service.inicializar(cancel_event=cancelled)
    assert error.value.code == "UPDATE_CANCELLED"
    with pytest.raises(PrevisaoError) as error:
        service.consultar()
    assert error.value.code == "MODEL_UNAVAILABLE"


def test_recalcular_publica_nova_execucao_sem_mudar_historico(configured, monkeypatch):
    service, paths, _ = configured
    service.carregar(paths["manifest"])
    source = service.root / "data/processed/reviewed.csv"
    before = source.read_bytes()
    monkeypatch.setattr(adapter, "hoje_manaus", lambda: date(2026, 1, 11))
    updated = service.recalcular("2026-01-10", 5)
    assert updated["run_id"] != json.loads(paths["manifest"].read_text())["run_id"]
    assert service.consultar(horizonte_dias=5)["execucao_id"] == updated["run_id"]
    assert source.read_bytes() == before


@pytest.mark.parametrize("cutoff", ["2026-01-11", "2026-01-12", "2026-1-1", "inválida"])
def test_recalculo_exige_dia_completo_e_formato_estrito(configured, monkeypatch, cutoff):
    service, _, _ = configured
    monkeypatch.setattr(adapter, "hoje_manaus", lambda: date(2026, 1, 11))
    with pytest.raises(PrevisaoError) as error:
        service.recalcular(cutoff, 5)
    assert error.value.code == "INVALID_INPUT"


def test_revisao_nao_configurada_e_corte_fora_da_revisao_preservam_ativa(configured, monkeypatch):
    service, paths, _ = configured
    service.carregar(paths["manifest"])
    monkeypatch.setattr(adapter, "hoje_manaus", lambda: date(2026, 1, 13))
    with pytest.raises(PrevisaoError) as error:
        service.recalcular("2026-01-12", 5)
    assert error.value.code == "UPDATE_FAILED"
    assert service.consultar()["execucao_id"] == json.loads(paths["manifest"].read_text())["run_id"]
    service.app.config["FORECAST_REVIEW"] = ""
    with pytest.raises(PrevisaoError) as error:
        service.recalcular("2026-01-10", 5)
    assert error.value.code == "REVIEW_UNAVAILABLE"


def test_cancelamento_antes_da_ativacao_preserva_execucao_anterior(configured, monkeypatch):
    service, paths, _ = configured
    service.carregar(paths["manifest"])
    event = Event()
    original = adapter.run_forecast
    def cancelled(*args, **kwargs):
        result = original(*args, **kwargs)
        event.set()
        return result
    monkeypatch.setattr(adapter, "run_forecast", cancelled)
    monkeypatch.setattr(adapter, "hoje_manaus", lambda: date(2026, 1, 11))
    with pytest.raises(PrevisaoError) as error:
        service.recalcular("2026-01-10", 5, cancel_event=event)
    assert error.value.code == "UPDATE_CANCELLED"
    assert service.consultar()["execucao_id"] == json.loads(paths["manifest"].read_text())["run_id"]


def test_cli_carregar_nao_expoe_previsoes_ou_codigos(configured):
    service, paths, _ = configured
    runner = service.app.test_cli_runner()
    response = runner.invoke(args=["previsao", "carregar", "--manifest", str(paths["manifest"])])
    assert response.exit_code == 0, response.output
    assert "00110" not in response.output and "00220" not in response.output
    assert "persistida localmente" in response.output
    response = runner.invoke(args=["previsao", "carregar", "--manifest", "data/artifacts/inexistente.json"])
    assert response.exit_code != 0 and "INVALID_ARTIFACT" in response.output


def test_associar_somente_vinculo_explicito_e_repeticao_sem_duplicar(configured):
    service, paths, (_, unmapped, _) = configured
    service.carregar(paths["manifest"])
    confirmation = {"confirmar": True, "operador": "Operador fictício", "motivo": "Conferência fictícia explícita."}
    service.associar_produto(unmapped, "00220", **confirmation)
    service.associar_produto(unmapped, "00220", **confirmation)
    assert service.consultar(unmapped)["codigo_produto"] == "00220"
    with service.app.app_context():
        assert db.session.get(Produto, unmapped).codigo_externo == "00220"
    response = service.app.test_cli_runner().invoke(args=["previsao", "associar-produto",
        "--produto-id", str(unmapped), "--codigo-externo", "00220", "--confirmar",
        "--operador", "Operador fictício", "--motivo", "Conferência fictícia explícita."])
    assert response.exit_code == 0 and "00220" not in response.output


@pytest.mark.parametrize("index,code,error_code", [(1, "00110", "MAPPING_CONFLICT"),
    (0, "00220", "MAPPING_CONFLICT"), (1, "99999", "HISTORY_NOT_FOUND"),
    (1, " 00220", "INVALID_INPUT"), (1, 220, "INVALID_INPUT")])
def test_associacao_invalida_ou_conflitante_preserva_catalogo(configured, index, code, error_code):
    service, paths, ids = configured
    service.carregar(paths["manifest"])
    with pytest.raises(PrevisaoError) as error:
        service.associar_produto(ids[index], code)
    assert error.value.code == error_code
    with service.app.app_context():
        assert db.session.get(Produto, ids[0]).codigo_externo == "00110"
        assert db.session.get(Produto, ids[1]).codigo_externo is None
