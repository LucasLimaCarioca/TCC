"""API, tela e comando de alertas com catálogo e gateway inteiramente fictícios."""

import json
import subprocess
from types import SimpleNamespace

import pytest

from app.app import create_app
from app.database import db
from app.models.alerta_estoque import AlertaEstoque
from app.models.produto import Produto
from app.services import estoque_service


@pytest.fixture
def preventive_app(tmp_path):
    app = create_app({
        "TESTING": True,
        "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / 'ficticio.db'}",
        "FORECAST_PROJECT_ROOT": str(tmp_path),
        "PREDICTIVE_ALERT_HORIZON_DAYS": 11,
        "PREDICTIVE_ALERTS_AUTO_ENABLED": False,
    })
    with app.app_context():
        db.create_all()
        product = Produto(nome="Produto fictício", categoria="Categoria fictícia", sabor="Fictício",
                          preco=1, quantidade_disponivel=20, estoque_minimo=4, ativo=True)
        db.session.add(product)
        db.session.flush()
        app.config["FICTIONAL_PRODUCT_ID"] = product.id
        db.session.add(AlertaEstoque(tipo_item="produto", produto_id=product.id, tipo_alerta="risco_futuro",
                                    mensagem="Alerta fictício anterior", nivel="aviso", ativo=True))
        db.session.commit()
    yield app
    with app.app_context():
        db.session.remove()
        db.engine.dispose()


class FakeGateway:
    def __init__(self, result=None):
        self.calls = []
        self.result = result or {"status": "ok", "risco": True, "acao": "atualizado", "alerta_id": 1}

    def consultar_risco(self, produto_id, granularidade, horizonte_dias):
        self.calls.append(("consultar", produto_id, granularidade, horizonte_dias))
        return self.result

    def sincronizar_alerta_preventivo(self, produto_id, horizonte_dias):
        self.calls.append(("sincronizar", produto_id, horizonte_dias))
        return self.result

    def executar_estoque(self, command, **kwargs):
        return getattr(estoque_service, command)(**kwargs)


def _path(app, preventive=False):
    product = app.config["FICTIONAL_PRODUCT_ID"]
    return f"/api/estoque/alertas/preventivos/{product}" if preventive else f"/api/estoque/risco/{product}"


@pytest.mark.parametrize("payload,expected", [({}, 11), ({"horizonte_dias": 1}, 1), ({"horizonte_dias": 366}, 366)])
def test_post_usa_gateway_com_horizonte_validado(preventive_app, payload, expected):
    gateway = FakeGateway()
    preventive_app.extensions["agent_gateway"] = gateway
    response = preventive_app.test_client().post(_path(preventive_app, True), json=payload)
    assert response.status_code == 200
    assert response.get_json() == gateway.result
    assert gateway.calls == [("sincronizar", preventive_app.config["FICTIONAL_PRODUCT_ID"], expected)]


@pytest.mark.parametrize("payload", [[], None, "fictício", {"produto_id": 1}, {"horizonte_dias": None},
                                   {"horizonte_dias": True}, {"horizonte_dias": "7"},
                                   {"horizonte_dias": 7.5}, {"horizonte_dias": 0},
                                   {"horizonte_dias": 367}, {"horizonte_dias": 7, "extra": 1}])
def test_post_rejeita_payload_invalido_sem_chamar_gateway(preventive_app, payload):
    gateway = FakeGateway()
    preventive_app.extensions["agent_gateway"] = gateway
    response = preventive_app.test_client().post(_path(preventive_app, True), json=payload)
    assert response.status_code == 400
    assert response.get_json()["code"] == "INVALID_INPUT"
    assert gateway.calls == []


def test_post_requer_runtime_e_nao_resolve_alerta(preventive_app):
    response = preventive_app.test_client().post(_path(preventive_app, True), json={})
    assert response.status_code == 503
    with preventive_app.app_context():
        alert = AlertaEstoque.query.one()
        assert alert.ativo and alert.resolvido_em is None


@pytest.mark.parametrize("query,expected", [("", 11), ("?horizonte_dias=8&granularidade=semanal", 8)])
def test_get_risco_somente_consulta_preservando_alerta(preventive_app, query, expected):
    gateway = FakeGateway()
    preventive_app.extensions["agent_gateway"] = gateway
    with preventive_app.app_context():
        alert = AlertaEstoque.query.one()
        original = (alert.id, alert.mensagem, alert.ativo, alert.criado_em, alert.resolvido_em)
    response = preventive_app.test_client().get(_path(preventive_app) + query)
    assert response.status_code == 200
    assert gateway.calls == [("consultar", preventive_app.config["FICTIONAL_PRODUCT_ID"],
                              "semanal" if query else "diaria", expected)]
    with preventive_app.app_context():
        alert = AlertaEstoque.query.one()
        assert (alert.id, alert.mensagem, alert.ativo, alert.criado_em, alert.resolvido_em) == original


@pytest.mark.parametrize("query", ["?horizonte_dias=0", "?horizonte_dias=367", "?horizonte_dias=abc",
                                   "?granularidade=anual"])
def test_get_rejeita_parametros_invalidos(preventive_app, query):
    gateway = FakeGateway()
    preventive_app.extensions["agent_gateway"] = gateway
    response = preventive_app.test_client().get(_path(preventive_app) + query)
    assert response.status_code == 400 and gateway.calls == []


def test_previsao_indisponivel_preserva_alerta_existente(preventive_app):
    gateway = FakeGateway({"status": "previsao_indisponivel", "risco": None, "code": "MODEL_UNAVAILABLE",
                           "mensagem": "Previsão fictícia indisponível; alerta anterior preservado."})
    preventive_app.extensions["agent_gateway"] = gateway
    response = preventive_app.test_client().post(_path(preventive_app, True), json={})
    assert response.status_code == 200 and response.get_json()["risco"] is None
    with preventive_app.app_context():
        assert AlertaEstoque.query.one().ativo


def test_tela_separa_consulta_e_atualizacao_e_mostra_pendencia(preventive_app, monkeypatch):
    original_view = estoque_service.visao_estoque

    def pending_view():
        result = original_view()
        result["alertas"][0]["avaliacao"] = {"atual": False, "motivo": "Previsão fictícia vencida."}
        return result

    monkeypatch.setattr(estoque_service, "visao_estoque", pending_view)
    response = preventive_app.test_client().get("/estoque")
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    for expected in ["Risco futuro", "Avaliação pendente", "Previsão fictícia vencida.",
                     'id="risco-form"', 'id="preventivo-form"', 'value="11"',
                     "sem alterar os alertas registrados", "não representa demanda zero"]:
        assert expected in html


def test_tela_aceita_alertas_legados_sem_avaliacao(preventive_app, monkeypatch):
    original_view = estoque_service.visao_estoque

    def legacy_view():
        result = original_view()
        for alert in result["alertas"]:
            alert.pop("avaliacao", None)
        return result

    monkeypatch.setattr(estoque_service, "visao_estoque", legacy_view)
    response = preventive_app.test_client().get("/estoque")
    assert response.status_code == 200 and "Alerta fictício anterior" in response.get_data(as_text=True)


@pytest.mark.parametrize("horizon", [None, 9])
def test_cli_exporta_resultado_privado_sem_imprimir_conteudo(preventive_app, tmp_path, monkeypatch, horizon):
    from app import forecast_commands

    subprocess.run(["git", "init", "--quiet", str(tmp_path)], check=True, capture_output=True)
    (tmp_path / ".gitignore").write_text("/data/\n")
    gateway = FakeGateway({"status": "ok", "risco": False, "acao": "resolvido", "marcador": "PRIVADO-FICTICIO"})
    monkeypatch.setattr(forecast_commands, "_with_runtime", lambda operation: operation(preventive_app, gateway))
    args = ["previsao", "sincronizar-alerta", "--produto-id", str(preventive_app.config["FICTIONAL_PRODUCT_ID"])]
    if horizon is not None:
        args.extend(["--horizonte", str(horizon)])
    response = preventive_app.test_cli_runner().invoke(args=args)
    assert response.exit_code == 0, response.output
    assert gateway.calls == [("sincronizar", preventive_app.config["FICTIONAL_PRODUCT_ID"], horizon)]
    assert "Resultado privado: data/reports/" in response.output and "PRIVADO-FICTICIO" not in response.output
    files = list((tmp_path / "data/reports").glob("consulta-agente-*.json"))
    assert len(files) == 1 and json.loads(files[0].read_text()) == gateway.result


def test_cli_trata_erro_estoque_e_encerra_runtime(preventive_app, monkeypatch):
    from app.agents import gateway as gateway_module
    from app.services.estoque_service import EstoqueError

    runtime = SimpleNamespace(config=SimpleNamespace(startup_timeout=1),
                              agents={"previsao": SimpleNamespace(aguardar_inicializacao=lambda: None)})
    stopped = []
    runtime.call = lambda operation, **kwargs: operation()
    runtime.stop = lambda: stopped.append(True)
    fake_gateway = FakeGateway()

    def unavailable(*args):
        raise EstoqueError("PRODUCT_NOT_FOUND", "Produto fictício ausente.")

    fake_gateway.sincronizar_alerta_preventivo = unavailable
    monkeypatch.setattr(gateway_module, "start_runtime", lambda app: runtime)
    monkeypatch.setattr(gateway_module, "AgentGateway", lambda runtime: fake_gateway)
    response = preventive_app.test_cli_runner().invoke(
        args=["previsao", "sincronizar-alerta", "--produto-id", "1"])
    assert response.exit_code == 1 and "PRODUCT_NOT_FOUND" in response.output
    assert stopped == [True]
