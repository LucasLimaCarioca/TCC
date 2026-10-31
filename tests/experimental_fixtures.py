"""Seleção experimental de teste: todos os registros e evidências são fictícios."""

import json

import pytest

from app.database import db
from app.forecasting.service import run_forecast
from app.models.produto import Produto
from app.services import catalogacao_service as catalog


def selection_request(service, product_id, *, selected=True):
    """Confirmação explícita para um código já associado no fixture."""
    with service.app.app_context():
        listing = catalog.listar_catalogacao()
        product = next(row for row in listing["produtos"] if row["id"] == product_id)
        code = next(row for row in listing["codigos"] if row["produto_id"] == product_id)
        return {"acao": "revisar", "produto_id": product_id, "codigo_produto": code["codigo_produto"],
            "versao": code["versao"], "versao_produto": product["versao"],
            "tipo_vinculo": "experimental", "selecionado_previsao": selected,
            "produto_ficticio_confirmado": True, "compatibilidade": {
                "identidade_confirmada": True, "embalagem_confirmada": True,
                "unidade_confirmada": True, "descricao_historica": "Sorvete fictício de chocolate",
                "embalagem_historica": "caixa de 10L", "unidade_historica": "caixa",
                "unidade_simulada": "caixa"}, "confirmar": True,
            "operador": "Operador fictício", "motivo": "Conferência experimental inteiramente fictícia."}


def prepare_experimental_configured(configured):
    service, legacy_paths, ids = configured
    service.app.config["PREDICTIVE_ALERTS_AUTO_ENABLED"] = False
    service.carregar(legacy_paths["manifest"])
    with service.app.app_context():
        product = db.session.get(Produto, ids[0])
        # Identifica um perfil do catálogo simulado; não infere código a partir dele.
        product.nome = "caixa de 10L - chocolate"
        product.categoria, product.sabor = "caixa de 10L", "chocolate"
        db.session.commit()
        catalog.executar_catalogacao(**selection_request(service, product.id))
        context = catalog.snapshot_selecao_experimental(db.session)
    paths = run_forecast(service.root, service.app.config["FORECAST_REVIEW"],
        cutoff="2026-01-10", start="2026-01-01", horizon=10,
        codes=[item["codigo_produto"] for item in context["associacoes"]],
        experimental_context=context)
    service.carregar(paths["manifest"])
    return service, paths, ids


@pytest.fixture
def experimental_configured(configured):
    return prepare_experimental_configured(configured)
