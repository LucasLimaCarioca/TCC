"""A flag limita a interface HTTP sem acessar o banco privado do projeto."""

import pytest

from app.app import create_app


@pytest.mark.parametrize("value,expected", [(True, True), (False, False), ("1", True), ("0", False)])
def test_configuracao_explicita_tem_prioridade_sobre_ambiente(monkeypatch, value, expected):
    monkeypatch.setenv("CATALOGACAO_ENABLED", "0" if expected else "1")
    application = create_app({"TESTING": True, "CATALOGACAO_ENABLED": value,
                              "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:"})
    assert application.config["CATALOGACAO_ENABLED"] is expected


def test_testes_nao_herdam_flag_do_ambiente(monkeypatch):
    monkeypatch.setenv("CATALOGACAO_ENABLED", "0")
    application = create_app({"TESTING": True, "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:"})
    assert application.config["CATALOGACAO_ENABLED"] is True


@pytest.mark.parametrize("value,expected", [("0", False), ("1", True)])
def test_aplicacao_le_flag_do_ambiente(monkeypatch, value, expected):
    monkeypatch.setenv("CATALOGACAO_ENABLED", value)
    application = create_app({"SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:"})
    assert application.config["CATALOGACAO_ENABLED"] is expected


@pytest.mark.parametrize("value", ["false", "", 2, None])
def test_configuracao_invalida_tem_erro_explicito(value):
    with pytest.raises(ValueError, match="CATALOGACAO_ENABLED"):
        create_app({"TESTING": True, "CATALOGACAO_ENABLED": value,
                    "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:"})


def test_desativada_bloqueia_tela_e_apis_antes_dos_servicos(app, monkeypatch):
    app.config["CATALOGACAO_ENABLED"] = False

    def unexpected(*args, **kwargs):
        pytest.fail("A flag desativada não deve acessar os serviços de catalogação.")

    monkeypatch.setattr("app.routes.catalogacao_routes.listar_catalogacao", unexpected)
    monkeypatch.setattr("app.routes.catalogacao_routes.EstoqueClient.executar", unexpected)
    client = app.test_client()
    for method, path in (("GET", "/catalogacao"), ("GET", "/api/catalogacao"),
                         ("POST", "/api/catalogacao/operacoes")):
        response = client.open(path, method=method, json={"acao": "revisar"})
        assert response.status_code == 404
        assert response.headers["Cache-Control"] == "no-store"
        if path.startswith("/api/"):
            assert response.json["code"] == "FEATURE_DISABLED"


@pytest.mark.parametrize("enabled", [True, False])
def test_links_consistentes_em_todas_as_telas(client, app, enabled):
    app.config["CATALOGACAO_ENABLED"] = enabled
    for path in ("/", "/estoque", "/previsoes"):
        response = client.get(path)
        assert response.status_code == 200
        assert ('href="/catalogacao"' in response.get_data(as_text=True)) is enabled


def test_reativar_restaura_tela_e_apis(app, monkeypatch):
    client = app.test_client()
    app.config["CATALOGACAO_ENABLED"] = False
    assert client.get("/catalogacao").status_code == 404
    monkeypatch.setattr("app.routes.catalogacao_routes.listar_catalogacao", lambda: {"codigos": []})
    monkeypatch.setattr("app.routes.catalogacao_routes.EstoqueClient.executar",
                        lambda self, command, **data: {"status": "ok"})
    app.config["CATALOGACAO_ENABLED"] = True
    assert client.get("/catalogacao").status_code == 200
    assert client.get("/api/catalogacao").json == {"codigos": []}
    assert client.post("/api/catalogacao/operacoes", json={"acao": "revisar"}).json == {"status": "ok"}
