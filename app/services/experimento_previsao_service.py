"""Concilia a seleção confirmada com a publicação sem reescrever artefatos."""

from sqlalchemy import select

from app.models.catalogacao_historica import RevisaoAssociacaoHistorica


class ExperimentoError(ValueError):
    def __init__(self, code, message):
        self.code = code
        super().__init__(message)


def validar_publicacao_experimental(session, result, produto=None):
    """Exige o mesmo conjunto e as mesmas revisões capturados no treinamento.

    Publicações anteriores continuam legíveis enquanto não houver uma seleção
    experimental. Um vínculo experimental nunca usa uma publicação sem contexto.
    """
    from app.services.catalogacao_service import CatalogacaoError, validar_selecao_experimental

    context = result.get("experimento")
    review = session.get(RevisaoAssociacaoHistorica, produto.id) if produto is not None else None
    experimental_product = review is not None and review.tipo_vinculo == "experimental"
    if experimental_product and not review.selecionado_previsao:
        raise ExperimentoError("PRODUCT_NOT_SELECTED", "Este produto não está selecionado para a previsão experimental.")
    if context is None:
        selected = session.scalar(select(RevisaoAssociacaoHistorica.produto_id).where(
            RevisaoAssociacaoHistorica.tipo_vinculo == "experimental",
            RevisaoAssociacaoHistorica.selecionado_previsao.is_(True)).limit(1))
        if experimental_product or selected is not None:
            raise ExperimentoError("EXPERIMENTAL_RUN_REQUIRED",
                "A publicação anterior não representa a seleção experimental. Atualize a previsão para gerar uma nova execução.")
        return None
    try:
        validar_selecao_experimental(session, context)
    except (CatalogacaoError, ValueError, TypeError):
        raise ExperimentoError("SELECTION_CHANGED",
            "A seleção experimental ou sua revisão mudou. Atualize a previsão; a publicação anterior foi preservada.") from None
    if produto is not None:
        if not experimental_product or not any(item["produto_id"] == produto.id
                and item["codigo_produto"] == produto.codigo_externo for item in context["associacoes"]):
            raise ExperimentoError("PRODUCT_NOT_SELECTED",
                "O produto não pertence às associações experimentais desta publicação.")
    return context
