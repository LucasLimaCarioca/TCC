"""Contratos da interface de estoque com o catálogo fictício do fixture."""

from html.parser import HTMLParser
import re

import pytest


class StockDocument(HTMLParser):
    """Índice mínimo de elementos com sua árvore de ancestrais."""

    VOID_TAGS = {"area", "base", "br", "col", "embed", "hr", "img", "input",
                 "link", "meta", "param", "source", "track", "wbr"}

    def __init__(self, html):
        super().__init__()
        self.stack = []
        self.nodes = []
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        node = {"tag": tag, "attrs": dict(attrs), "ancestors": tuple(self.stack), "text": ""}
        self.nodes.append(node)
        if tag not in self.VOID_TAGS:
            self.stack.append(node)

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index]["tag"] == tag:
                del self.stack[index:]
                return

    def handle_data(self, text):
        for node in self.stack:
            node["text"] += text

    def by_id(self, identifier):
        matches = [node for node in self.nodes if node["attrs"].get("id") == identifier]
        assert len(matches) == 1
        return matches[0]

    def descendants(self, node):
        return [child for child in self.nodes if any(ancestor is node for ancestor in child["ancestors"])]


@pytest.fixture
def stock_document(app, client):
    app.config["PREDICTIVE_ALERT_HORIZON_DAYS"] = 11
    response = client.get("/estoque")
    assert response.status_code == 200
    return StockDocument(response.get_data(as_text=True))


@pytest.mark.parametrize("form_id,feedback_id", [
    ("materia-form", "materia-feedback"),
    ("movimentacao-form", "movimentacao-feedback"),
    ("minimo-form", "minimo-feedback"),
    ("sincronizar-form", "sincronizar-feedback"),
    ("risco-form", "risco-feedback"),
    ("preventivo-form", "preventivo-feedback"),
])
def test_feedback_acessivel_pertence_ao_formulario_responsavel(stock_document, form_id, feedback_id):
    document = stock_document
    form = document.by_id(form_id)
    feedback = document.by_id(feedback_id)
    assert any(ancestor is form for ancestor in feedback["ancestors"])
    assert feedback["attrs"]["role"] == "status"
    assert feedback["attrs"]["aria-live"] == "polite"
    assert feedback["attrs"]["aria-atomic"] == "true"
    assert feedback["attrs"]["tabindex"] == "-1"
    assert "hidden" in feedback["attrs"]
    assert "stock-form-feedback" in feedback["attrs"]["class"].split()


def test_cadastro_tem_controles_de_uma_unidade_e_precisao_manual(stock_document):
    document = stock_document
    form = document.by_id("materia-form")
    minimum = document.by_id("materia-minimo")
    assert any(ancestor is form for ancestor in minimum["ancestors"])
    assert minimum["attrs"]["name"] == "estoque_minimo"
    assert minimum["attrs"]["type"] == "text"
    assert minimum["attrs"]["inputmode"] == "decimal"
    assert "required" in minimum["attrs"]
    stepper = [ancestor for ancestor in minimum["ancestors"]
               if "data-decimal-stepper" in ancestor["attrs"]]
    assert len(stepper) == 1
    buttons = [node for node in document.descendants(stepper[0]) if node["tag"] == "button"]
    assert {button["attrs"]["data-step"] for button in buttons} == {"-1", "1"}
    assert all(button["attrs"]["type"] == "button" and button["attrs"].get("aria-label")
               for button in buttons)
    pattern = re.compile(minimum["attrs"]["pattern"])
    for valid in ["0", "1", "1.123456789", "1,123456789"]:
        assert pattern.fullmatch(valid)
    for invalid in ["-1", "1.1234567890", "1e2"]:
        assert not pattern.fullmatch(invalid)
    help_text = document.by_id(minimum["attrs"]["aria-describedby"])["text"]
    assert "unidade de medida cadastrada" in help_text
    assert "nove casas decimais" in help_text


def test_quantidade_inicial_e_opcional_com_zero_e_mesma_precisao(stock_document):
    document = stock_document
    form = document.by_id("materia-form")
    initial = [node for node in document.descendants(form)
               if node["attrs"].get("name") == "quantidade_inicial"]
    assert len(initial) == 1
    attrs = initial[0]["attrs"]
    assert "required" not in attrs
    assert attrs["value"] == "0"
    assert attrs["type"] == "text" and attrs["inputmode"] == "decimal"
    assert attrs["pattern"] == document.by_id("materia-minimo")["attrs"]["pattern"]
    assert "Quantidade inicial vazia significa zero" in document.by_id(attrs["aria-describedby"])["text"]


@pytest.mark.parametrize("form_id,input_id,name", [
    ("movimentacao-form", "movimentacao-quantidade", "quantidade"),
    ("minimo-form", "configurar-minimo", "estoque_minimo"),
])
def test_movimentacao_e_minimo_compartilham_controles_de_uma_unidade(stock_document, form_id, input_id, name):
    document = stock_document
    form = document.by_id(form_id)
    field = document.by_id(input_id)
    attrs = field["attrs"]
    assert any(ancestor is form for ancestor in field["ancestors"])
    assert attrs["name"] == name and attrs["type"] == "text"
    assert attrs["inputmode"] == "numeric" and attrs["pattern"] == "[0-9]+"
    assert "data-decimal" in attrs and "required" in attrs
    stepper = next(ancestor for ancestor in field["ancestors"] if "data-decimal-stepper" in ancestor["attrs"])
    buttons = [node for node in document.descendants(stepper) if node["tag"] == "button"]
    assert {button["attrs"].get("data-step") for button in buttons} == {"-1", "1"}
    assert all(button["attrs"].get("type") == "button" and button["attrs"].get("aria-label") for button in buttons)
    help_text = document.by_id(attrs["aria-describedby"])["text"]
    assert "ponto ou vírgula" in help_text and "nove casas decimais" in help_text


def test_alerta_persistente_exibe_apenas_horizonte_oficial_consulta_aceita_personalizado(stock_document):
    document = stock_document
    persistent = document.by_id("preventivo-form")
    risk = document.by_id("risco-form")
    assert not any(node["attrs"].get("name") == "horizonte_dias"
                   for node in document.descendants(persistent))
    assert "Horizonte oficial: 11 dias" in persistent["text"]
    horizons = [node for node in document.descendants(risk)
                if node["attrs"].get("name") == "horizonte_dias"]
    assert len(horizons) == 1
    assert horizons[0]["attrs"]["value"] == "11"
    assert horizons[0]["attrs"]["min"] == "1" and horizons[0]["attrs"]["max"] == "366"
    assert "sem alterar os alertas registrados" in risk["text"]


def test_navegacao_oferece_consulta_de_alertas_e_script_local(stock_document):
    document = stock_document
    panel = document.by_id("estoque-notificacoes")
    assert panel["tag"] == "details"
    assert panel["attrs"]["data-api"] == "/api/estoque/alertas"
    descendants = document.descendants(panel)
    assert any(node["tag"] == "summary" and node["attrs"].get("aria-label")
               for node in descendants)
    assert any(node["tag"] == "a" and node["attrs"].get("href") == "/estoque#alertas-estoque"
               for node in descendants)
    assert document.by_id("notificacoes-status")["attrs"]["aria-live"] == "polite"
    document.by_id("notificacoes-contagem")
    scripts = [node["attrs"] for node in document.nodes if node["tag"] == "script"]
    assert any(attrs.get("src") == "/static/js/notificacoes_estoque.js" and "defer" in attrs
               for attrs in scripts)
