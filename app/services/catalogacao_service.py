"""Catalogação humana de códigos, com revisão, concorrência e reversão auditável.

Não consulta o CSV original, não infere códigos por IDs e nunca altera vendas ou
publicações de previsão. Os saldos só são escritos no cadastro inicial explícito.
"""

from datetime import datetime
from decimal import Decimal, InvalidOperation
import hashlib
import json
import math
import re
import unicodedata
from uuid import UUID, uuid4

from flask import current_app
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.database import db
from app.models.catalogacao_historica import OperacaoCatalogacao, RevisaoAssociacaoHistorica
from app.models.movimentacao_estoque import MovimentacaoEstoque
from app.models.previsao_execucao import PrevisaoExecucao
from app.models.produto import Produto
from app.models.timestamps import utc_now
from app.models.venda_historica import VendaHistorica
from app.services.estoque_service import EstoqueError, _atualizar_alerta, _transacao, inteiro


class CatalogacaoError(EstoqueError):
    pass


def _error(code, message):
    raise CatalogacaoError(code, message)


def _hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                    allow_nan=False, separators=(",", ":")).encode()).hexdigest()


def _code(value):
    if (type(value) is not str or not 1 <= len(value) <= 64 or value != value.strip()
            or any(char.isspace() or ord(char) < 32 or ord(char) == 127 for char in value)):
        _error("INVALID_INPUT", "Informe o código histórico textual completo, sem espaços.")
    return value


def _text(value, name, limit):
    if (type(value) is not str or not value.strip() or len(value.strip()) > limit
            or any(ord(char) < 32 or ord(char) == 127 for char in value)):
        _error("INVALID_INPUT", f"Informe {name} com até {limit} caracteres.")
    return value.strip()


def _id(value):
    if type(value) is not int or value <= 0:
        _error("INVALID_INPUT", "Informe um ID interno de produto válido, separado do código histórico.")
    return value


def _units(value):
    if type(value) is str and re.fullmatch(r"[0-9]+", value):
        if len(value) > 30:
            _error("INVALID_QUANTITY", "Informe uma quantidade inteira válida.")
        value = int(value)
    return inteiro(value, 0)


def _price(value):
    if type(value) not in (str, int, float, Decimal):
        _error("INVALID_INPUT", "Informe um preço não negativo válido.")
    try:
        amount = Decimal(str(value).replace(",", "."))
        result = float(amount)
        if not amount.is_finite() or amount < 0 or not math.isfinite(result) or Decimal(str(result)) != amount:
            raise InvalidOperation
    except (InvalidOperation, ValueError, OverflowError):
        _error("INVALID_INPUT", "Informe um preço não negativo representável pelo cadastro de produtos.")
    return result


def fingerprint_produto(produto):
    """Identidade comercial; movimentos e mudanças de mínimo não invalidam revisão."""
    return _hash({"id": produto.id, "codigo_externo": produto.codigo_externo,
                  "nome": produto.nome, "categoria": produto.categoria, "sabor": produto.sabor,
                  "preco": str(produto.preco), "descricao": produto.descricao,
                  "ativo": produto.ativo, "demonstracao": bool(produto.demonstracao)})


def _historico(session):
    metadata = {}

    def add(code, category=None, group=None, origin=None):
        if type(code) is not str:
            return
        _code(code)
        entry = metadata.setdefault(code, {"categorias": set(), "grupos": set(), "origens": set()})
        if category:
            entry["categorias"].add(category)
        if group:
            entry["grupos"].add(group)
        if origin:
            entry["origens"].add(origin)

    for code, group, category in session.execute(select(VendaHistorica.codigo_produto,
            VendaHistorica.codigo_grupo, VendaHistorica.categoria).distinct()):
        add(code, category, group, "historico_importado")
    active = session.scalar(select(PrevisaoExecucao).where(PrevisaoExecucao.ativa.is_(True)))
    if active is not None:
        result = active.resultado_json if type(active.resultado_json) is dict else {}
        forecast = result.get("forecast", {})
        products = forecast.get("products", {}) if type(forecast) is dict else {}
        if type(products) is dict:
            for code in products:
                add(code, origin="previsao_publicada")
        # Metadados de grupo/categoria estão no relatório privado, não nos nomes
        # operacionais nem no ID interno. Falha de acesso mantém ausência explícita.
        from app.services.previsao_service import PrevisaoService, PrevisaoError
        try:
            service = PrevisaoService(current_app._get_current_object())
            _, _, bundle, report = service._bundle(active.manifest_path, active.manifest_sha256,
                                                  check_all=False)
            if bundle == active.resultado_json:
                for code, item in report.get("dataset", {}).get("products", {}).items():
                    add(code, item.get("categoria"), item.get("codigo_grupo"), "previsao_publicada")
        except PrevisaoError:
            pass
    return {code: {key: sorted(values) for key, values in entry.items()}
            for code, entry in metadata.items()}


def _metadata_hash(metadata, code):
    entry = metadata.get(code, {})
    return _hash({"categorias": entry.get("categorias", []), "grupos": entry.get("grupos", [])})


def _review_dict(review):
    if review is None:
        return None
    return {"codigo_produto": review.codigo_produto,
            "assinatura_produto": review.assinatura_produto,
            "assinatura_historico": review.assinatura_historico,
            "operador": review.operador, "motivo": review.motivo,
            "revisado_em": review.revisado_em.isoformat(),
            "tipo_vinculo": review.tipo_vinculo or "operacional",
            "selecionado_previsao": bool(review.selecionado_previsao),
            "compatibilidade": review.compatibilidade_json}


def _normalized_snapshot(snapshot):
    """Auditorias 0009 permanecem reversíveis sem reescrever seu JSON original."""
    value = dict(snapshot)
    if value.get("revisao") is not None:
        value["revisao"] = {"tipo_vinculo": "operacional", "selecionado_previsao": False,
                             "compatibilidade": None, **value["revisao"]}
    return value


def _snapshot(session, product):
    return {"produto_id": product.id, "codigo_externo": product.codigo_externo,
            "assinatura_produto": fingerprint_produto(product),
            "revisao": _review_dict(session.get(RevisaoAssociacaoHistorica, product.id))}


def versao_produto(session, product):
    return _hash(_snapshot(session, product))


def _reviewed(session, product, metadata):
    if product is None or not product.codigo_externo:
        return False
    review = session.get(RevisaoAssociacaoHistorica, product.id)
    if review is None:
        return False
    if review.tipo_vinculo == "experimental":
        try:
            _experimental_compatibility(product, review.compatibilidade_json, stored=True)
        except CatalogacaoError:
            return False
    elif product.demonstracao or review.tipo_vinculo != "operacional":
        return False
    return bool(review.codigo_produto == product.codigo_externo
        and review.assinatura_produto == fingerprint_produto(product)
        and product.codigo_externo in metadata
        and review.assinatura_historico == _metadata_hash(metadata, product.codigo_externo))


def associacao_revisada(session, produto):
    # Legados pendentes não exigem reler todo o histórico a cada consulta/RPC.
    if (produto is None or not produto.codigo_externo
            or session.get(RevisaoAssociacaoHistorica, produto.id) is None):
        return False
    return _reviewed(session, produto, _historico(session))


def motivo_associacao(session, produto):
    if produto is None or not produto.codigo_externo:
        return "Produto sem associação histórica explícita."
    if not associacao_revisada(session, produto):
        return "Associação histórica pendente de revisão explícita no catálogo."
    review = session.get(RevisaoAssociacaoHistorica, produto.id)
    if review.tipo_vinculo == "experimental":
        return "Associação experimental revisada: histórico real, estoque e vendas operacionais simulados."
    return "Associação histórica operacional revisada pelo operador."


def _normal(value):
    value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode().upper()
    return "".join(word for word in re.findall(r"[A-Z0-9]+", value) if word not in {"DE", "DA", "DO"})


def elegivel_experimento(product):
    """Verifica o perfil dos onze cadastros; nunca procura um código histórico."""
    from app.services.demonstracao_service import PRODUCTS
    return any(product.nome == f"{category} - {flavour}"
               and product.categoria == category and product.sabor == flavour
               for category, flavour, _ in PRODUCTS)


def _experimental_compatibility(product, compatibility, *, fictitious=None, stored=False):
    if not elegivel_experimento(product):
        _error("EXPERIMENTAL_PRODUCT_REQUIRED", "Selecione um dos onze produtos fictícios já definidos.")
    flags = {"identidade_confirmada", "embalagem_confirmada", "unidade_confirmada"}
    fields = {"descricao_historica": 500, "embalagem_historica": 100,
              "unidade_historica": 64, "unidade_simulada": 64}
    expected = flags | set(fields) | ({"produto_ficticio_confirmado"} if stored else set())
    if type(compatibility) is not dict or set(compatibility) != expected:
        _error("INCOMPATIBLE_ASSOCIATION", "Informe a identidade comercial, embalagem e unidade conferidas.")
    if any(compatibility.get(key) is not True for key in flags):
        _error("INCOMPATIBLE_ASSOCIATION", "A associação experimental exige compatibilidade comercial, de embalagem e de unidade confirmada.")
    confirmed = compatibility.get("produto_ficticio_confirmado") if stored else fictitious
    if confirmed is not True:
        _error("CONFIRMATION_REQUIRED", "Confirme que o produto utiliza estoque e vendas operacionais simulados.")
    normalized = {key: _text(compatibility.get(key), key.replace("_", " "), limit)
                  for key, limit in fields.items()}
    # Pontuação pode fazer parte da unidade (por exemplo, uma razão); não usar
    # a normalização de categorias que remove separadores e palavras.
    unit = lambda value: " ".join(unicodedata.normalize("NFKC", value).casefold().split())
    if unit(normalized["unidade_historica"]) != unit(normalized["unidade_simulada"]):
        _error("INCOMPATIBLE_ASSOCIATION", "Unidades diferentes não podem ser associadas sem conversão; selecione uma equivalência na mesma unidade.")
    # Uma declaração humana não pode aprovar uma contradição dimensional explícita.
    def litres(value):
        found = re.findall(r"(?i)(\d+(?:[.,]\d+)?)\s*(?:l|litros?)\b", value)
        return {Decimal(item.replace(",", ".")) for item in found}
    source_volume, target_volume = litres(normalized["embalagem_historica"]), litres(product.categoria)
    if source_volume and target_volume and source_volume != target_volume:
        _error("INCOMPATIBLE_ASSOCIATION", "A capacidade da embalagem histórica difere da embalagem simulada.")
    return {**normalized, **{flag: True for flag in flags}, "produto_ficticio_confirmado": True}


def snapshot_selecao_experimental(session):
    """Seleção auditada e imutável para identificar uma nova execução de previsão."""
    from app.forecasting.experimental import context_signature
    reviews = session.scalars(select(RevisaoAssociacaoHistorica).where(
        RevisaoAssociacaoHistorica.selecionado_previsao.is_(True))).all()
    if not reviews:
        _error("SELECTION_EMPTY", "Selecione associações experimentais compatíveis antes de atualizar a previsão.")
    metadata, selected = _historico(session), []
    for review in reviews:
        product = session.get(Produto, review.produto_id)
        if (review.tipo_vinculo != "experimental" or product is None or not product.ativo
                or not _reviewed(session, product, metadata)):
            _error("SELECTION_INVALID", "Uma associação selecionada exige nova revisão de compatibilidade.")
        selected.append({"produto_id": product.id, "codigo_produto": review.codigo_produto,
                         "versao_revisao": versao_produto(session, product)})
    if len(selected) > 11:
        _error("SELECTION_INVALID", "A seleção experimental está limitada aos onze produtos fictícios.")
    context = {"versao": 1, "natureza": "experimental", "origem_historico": "real",
               "estoque": "simulado", "vendas_operacionais": "simuladas",
               "associacoes": sorted(selected, key=lambda item: item["codigo_produto"])}
    context["assinatura"] = context_signature(context)
    return context


def validar_selecao_experimental(session, contexto):
    from app.forecasting.experimental import validate_experimental_context
    try:
        expected = validate_experimental_context(contexto)
    except (ValueError, TypeError):
        _error("SELECTION_INVALID", "A identificação da seleção experimental é inválida.")
    try:
        actual = snapshot_selecao_experimental(session)
    except CatalogacaoError:
        _error("SELECTION_CHANGED", "A seleção experimental mudou; atualize a previsão com a seleção atual.")
    if actual != expected:
        _error("SELECTION_CHANGED", "A seleção experimental mudou; atualize a previsão com a seleção atual.")
    return True


def _warnings(product, entry):
    warnings = []
    if product is not None:
        if product.demonstracao:
            warnings.append("Produto identificado como demonstração; vínculo exige revisão e não habilita previsão operacional.")
        categories = entry.get("categorias", [])
        if categories and _normal(product.categoria) not in {_normal(value) for value in categories}:
            warnings.append("Categoria operacional difere da categoria histórica. Diferença é indício, não prova de erro.")
    if len({_normal(value) for value in entry.get("categorias", [])}) > 1:
        warnings.append("Código possui mais de uma categoria no histórico; confira os metadados.")
    return warnings


def _owner(session, code):
    return session.scalar(select(Produto).where(Produto.codigo_externo == code))


def _code_version(session, code, metadata):
    product = _owner(session, code)
    return _hash({"codigo_produto": code, "historico": _metadata_hash(metadata, code),
                  "produto": _snapshot(session, product) if product is not None else None})


def listar_catalogacao():
    """GET sem materializar revisões nem reservar a escrita."""
    with Session(db.engine) as session:
        metadata = _historico(session)
        products = session.scalars(select(Produto).order_by(Produto.nome, Produto.id)).all()
        owners = {product.codigo_externo: product for product in products if product.codigo_externo}
        codes = []
        for code in sorted(set(metadata) | set(owners)):
            product, entry = owners.get(code), metadata.get(code, {"categorias": [], "grupos": [], "origens": []})
            warnings = _warnings(product, entry)
            if code not in metadata:
                warnings.append("Código associado não está no histórico importado nem na publicação disponível.")
            reviewed = _reviewed(session, product, metadata)
            review = session.get(RevisaoAssociacaoHistorica, product.id) if product else None
            state = ("revisado" if reviewed else "sem_associacao" if product is None else
                     "possivel_inconsistencia" if warnings else "pendente")
            codes.append({"codigo_produto": code, **entry, "produto_id": product.id if product else None,
                "nome": product.nome if product else None, "estado": state, "revisado": reviewed,
                "avisos": warnings, "versao": _code_version(session, code, metadata),
                "tipo_vinculo": review.tipo_vinculo if review else "operacional",
                "selecionado_previsao": bool(review and review.selecionado_previsao),
                "compatibilidade": review.compatibilidade_json if review else None})
        operations = session.scalars(select(OperacaoCatalogacao).order_by(
            OperacaoCatalogacao.criado_em.desc(), OperacaoCatalogacao.id.desc()).limit(100)).all()
        reversed_ids = set(session.scalars(select(OperacaoCatalogacao.reverte_id).where(
            OperacaoCatalogacao.reverte_id.is_not(None))))
        audit = []
        for operation in operations:
            ids = [item["produto_id"] for item in operation.depois_json]
            reversible = operation.acao != "reverter" and operation.id not in reversed_ids
            for item in operation.depois_json:
                current = session.get(Produto, item["produto_id"])
                reversible = reversible and current is not None and _snapshot(session, current) == _normalized_snapshot(item)
            audit.append({"id": operation.id, "acao": operation.acao, "operador": operation.operador,
                "motivo": operation.motivo, "criado_em": operation.criado_em.isoformat(),
                "reverte_id": operation.reverte_id, "produtos": ids,
                "codigos": sorted({item["codigo_externo"] for item in
                    operation.antes_json + operation.depois_json if item["codigo_externo"]}),
                "tipos_vinculo": sorted({item["revisao"].get("tipo_vinculo", "operacional")
                    for item in operation.antes_json + operation.depois_json if item.get("revisao")}),
                "reversivel": bool(reversible)})
        return {"codigos": codes, "produtos": [{"id": product.id, "nome": product.nome,
            "categoria": product.categoria, "sabor": product.sabor, "preco": product.preco,
            "ativo": product.ativo, "demonstracao": bool(product.demonstracao),
            "elegivel_experimento": elegivel_experimento(product),
            "codigo_externo": product.codigo_externo, "versao": versao_produto(session, product)}
            for product in products], "operacoes": audit}


def _review(session, product, metadata, operator, reason, *, tipo_vinculo="operacional",
            selecionado_previsao=False, compatibilidade=None):
    review = session.get(RevisaoAssociacaoHistorica, product.id)
    if review is None:
        review = RevisaoAssociacaoHistorica(produto_id=product.id)
        session.add(review)
    review.codigo_produto = product.codigo_externo
    review.assinatura_produto = fingerprint_produto(product)
    review.assinatura_historico = _metadata_hash(metadata, product.codigo_externo)
    review.operador, review.motivo, review.revisado_em = operator, reason, utc_now()
    review.tipo_vinculo = tipo_vinculo
    review.selecionado_previsao = selecionado_previsao
    review.compatibilidade_json = compatibilidade


def _clear_review(session, product):
    review = session.get(RevisaoAssociacaoHistorica, product.id)
    if review is not None:
        session.delete(review)


def _expect_product(session, product, expected):
    if type(expected) is not str or expected != versao_produto(session, product):
        _error("CATALOG_CHANGED", "O produto mudou desde a consulta. Atualize e confirme novamente.")


def _create_product(session, fields):
    if type(fields) is not dict or set(fields) - {
            "nome", "categoria", "sabor", "preco", "estoque_inicial", "estoque_minimo", "ativo", "descricao"}:
        _error("INVALID_INPUT", "Informe os dados comerciais do novo produto.")
    if not {"nome", "categoria", "sabor", "preco", "estoque_inicial", "estoque_minimo"} <= set(fields):
        _error("INVALID_INPUT", "Nome, categoria, sabor, preço, estoque inicial e mínimo são obrigatórios.")
    active = fields.get("ativo", False)
    if type(active) is not bool:
        _error("INVALID_INPUT", "A ativação do produto exige uma escolha explícita válida.")
    initial, minimum = _units(fields["estoque_inicial"]), _units(fields["estoque_minimo"])
    name = _text(fields["nome"], "nome", 100)
    if session.scalar(select(Produto.id).where(Produto.nome == name)) is not None:
        _error("MAPPING_CONFLICT", "Nome de produto já cadastrado; selecione o produto existente.")
    description = fields.get("descricao")
    if description is not None and description != "":
        description = _text(description, "descrição", 255)
    else:
        description = None
    product = Produto(nome=name, categoria=_text(fields["categoria"], "categoria", 100),
        sabor=_text(fields["sabor"], "sabor", 100), preco=_price(fields["preco"]),
        descricao=description, quantidade_disponivel=initial, estoque_minimo=minimum,
        ativo=active, demonstracao=False)
    session.add(product)
    session.flush()
    if initial > 0:
        movement = MovimentacaoEstoque(tipo_item="produto", produto_id=product.id,
            tipo_movimentacao="entrada", quantidade=initial, saldo_anterior=0,
            saldo_posterior=initial, motivo="Cadastro inicial de produto operacional por catalogação histórica.")
        session.add(movement)
        session.flush()
        session.refresh(movement)
        if movement.quantidade != initial or movement.saldo_posterior != initial:
            _error("INVALID_QUANTITY", "O estoque inicial não pode ser auditado com a precisão informada.")
    _atualizar_alerta(session, "produto", product)
    return product


def _restore_review(session, product, data):
    _clear_review(session, product)
    session.flush()
    if data is not None:
        session.add(RevisaoAssociacaoHistorica(produto_id=product.id,
            codigo_produto=data["codigo_produto"], assinatura_produto=data["assinatura_produto"],
            assinatura_historico=data["assinatura_historico"], operador=data["operador"],
            motivo=data["motivo"], revisado_em=datetime.fromisoformat(data["revisado_em"]),
            tipo_vinculo=data.get("tipo_vinculo", "operacional"),
            selecionado_previsao=data.get("selecionado_previsao", False),
            compatibilidade_json=data.get("compatibilidade")))


def _reverse(session, data):
    target = data.get("reverter_operacao_id")
    if type(target) is not str:
        _error("INVALID_INPUT", "Informe a operação de catalogação a reverter.")
    original = session.get(OperacaoCatalogacao, target)
    if original is None or original.acao == "reverter":
        _error("MAPPING_CONFLICT", "Operação não disponível para reversão de vínculo.")
    if session.scalar(select(OperacaoCatalogacao.id).where(OperacaoCatalogacao.reverte_id == target)):
        _error("MAPPING_CONFLICT", "Esta operação já foi revertida.")
    versions = data.get("versoes")
    if type(versions) is not dict or set(versions) != {str(item["produto_id"]) for item in original.depois_json}:
        _error("INVALID_INPUT", "Atualize e confirme as versões de todos os produtos envolvidos.")
    products = []
    for after in original.depois_json:
        product = session.get(Produto, after["produto_id"])
        if product is None:
            _error("MAPPING_CONFLICT", "Um produto da operação não está mais disponível.")
        _expect_product(session, product, versions[str(product.id)])
        if _snapshot(session, product) != _normalized_snapshot(after):
            _error("CATALOG_CHANGED", "Uma operação posterior alterou o vínculo; não é seguro reverter esta operação.")
        products.append(product)
    before = [_snapshot(session, product) for product in products]
    # Liberar todos os códigos antes de restaurar evita conflitos transitórios.
    for product in products:
        product.codigo_externo = None
        _clear_review(session, product)
    session.flush()
    desired = {item["produto_id"]: item for item in original.antes_json}
    for product in products:
        state = desired[product.id]
        code = state["codigo_externo"]
        if code is not None:
            owner = _owner(session, code)
            if owner is not None and owner.id != product.id:
                _error("MAPPING_CONFLICT", "O código já está associado a outro produto. Reversão cancelada.")
        product.codigo_externo = code
        _restore_review(session, product, state["revisao"])
    session.flush()
    return products, before, target, None


def executar_catalogacao(acao, **dados):
    """Mutação chamada pela autoridade Estoque, local ou no seu worker SPADE."""
    if type(acao) is not str or acao not in {"associar", "criar", "revisar", "alterar", "desassociar", "reverter"}:
        _error("INVALID_INPUT", "Operação de catalogação inválida.")
    allowed = {"confirmar", "operador", "motivo", "operacao_id"}
    allowed |= ({"reverter_operacao_id", "versoes"} if acao == "reverter" else
                {"codigo_produto", "versao", "produto"} if acao == "criar" else
                {"codigo_produto", "versao", "produto_id", "versao_produto"})
    if acao != "reverter":
        allowed |= {"tipo_vinculo", "selecionado_previsao", "produto_ficticio_confirmado", "compatibilidade"}
    if set(dados) - allowed:
        _error("INVALID_INPUT", "A operação contém campos desconhecidos.")
    if dados.get("confirmar") is not True:
        _error("CONFIRMATION_REQUIRED", "Confirme explicitamente o código e o produto antes de alterar o catálogo.")
    operator = _text(dados.get("operador"), "identificação do operador", 100)
    reason = _text(dados.get("motivo"), "motivo da revisão", 500)
    kind = dados.get("tipo_vinculo", "operacional")
    selected = dados.get("selecionado_previsao", False)
    if acao != "reverter":
        if type(kind) is not str or kind not in {"operacional", "experimental"}:
            _error("INVALID_INPUT", "Identifique o vínculo como operacional ou experimental.")
        if type(selected) is not bool or selected and kind != "experimental":
            _error("INVALID_INPUT", "Somente associações experimentais podem participar desta seleção de previsão.")
        if acao == "criar" and kind == "experimental":
            _error("EXPERIMENTAL_CREATE_DISABLED", "O experimento usa somente os onze produtos fictícios existentes; não cria produtos.")
    operation_id = dados.get("operacao_id")
    try:
        operation_id = str(UUID(operation_id)) if operation_id is not None else str(uuid4())
        digest = _hash({"acao": acao, "dados": {key: value for key, value in dados.items() if key != "operacao_id"}})
    except (ValueError, TypeError, AttributeError):
        _error("INVALID_INPUT", "Informe uma chave de operação UUID e dados JSON válidos.")
    try:
        with _transacao() as session:
            previous = session.get(OperacaoCatalogacao, operation_id)
            if previous is not None:
                if previous.request_hash != digest:
                    _error("IDEMPOTENCY_CONFLICT", "Chave da operação já utilizada com outro conteúdo.")
                return previous.resultado_json
            if acao == "reverter":
                products, before, reverse_id, code = _reverse(session, dados)
            else:
                code = _code(dados.get("codigo_produto"))
                metadata = _historico(session)
                if acao != "desassociar" and code not in metadata:
                    _error("HISTORY_NOT_FOUND", "O código não está no histórico importado nem na previsão disponível.")
                if type(dados.get("versao")) is not str or dados["versao"] != _code_version(session, code, metadata):
                    _error("CATALOG_CHANGED", "O código ou seu vínculo mudou. Atualize a lista e confirme novamente.")
                owner = _owner(session, code)
                if acao == "criar":
                    if owner is not None:
                        _error("MAPPING_CONFLICT", "O código já está associado; revise o vínculo existente.")
                    product = _create_product(session, dados.get("produto"))
                else:
                    product = session.get(Produto, _id(dados.get("produto_id")))
                    if product is None:
                        _error("PRODUCT_NOT_FOUND", "Produto operacional não encontrado.")
                    _expect_product(session, product, dados.get("versao_produto"))
                if product.demonstracao and acao != "desassociar" and kind != "experimental":
                    _error("DEMONSTRATION_PRODUCT", "Produtos de demonstração não podem receber associações históricas operacionais.")
                products = [product]
                if acao in {"revisar", "desassociar"}:
                    if owner is None or owner.id != product.id:
                        _error("MAPPING_CONFLICT", "A revisão deve identificar o produto atualmente associado ao código.")
                elif acao == "alterar":
                    if owner is None or owner.id == product.id or product.codigo_externo is not None:
                        _error("MAPPING_CONFLICT", "Selecione um vínculo existente e um produto de destino sem associação.")
                    products = [owner, product]
                elif owner is not None or product.codigo_externo is not None:
                    _error("MAPPING_CONFLICT", "Código ou produto já possui uma associação; use a alteração explícita de vínculo.")
                before = [_snapshot(session, item) for item in products]
                if acao == "desassociar":
                    product.codigo_externo = None
                    _clear_review(session, product)
                else:
                    previous_reviews = [session.get(RevisaoAssociacaoHistorica, item.id) for item in products]
                    if ("tipo_vinculo" not in dados and any(review is not None
                            and review.tipo_vinculo == "experimental" for review in previous_reviews)):
                        _error("EXPERIMENTAL_MODE_REQUIRED", "Identifique explicitamente a natureza experimental antes de revisar este vínculo.")
                    compatibility = None
                    if kind == "experimental":
                        compatibility = _experimental_compatibility(product, dados.get("compatibilidade"),
                            fictitious=dados.get("produto_ficticio_confirmado"))
                        if selected and not product.ativo:
                            _error("SELECTION_INVALID", "Um produto inativo não pode participar da previsão de estoque simulado.")
                    if acao == "alterar":
                        owner.codigo_externo = None
                        _clear_review(session, owner)
                        session.flush()
                    product.codigo_externo = code
                    _review(session, product, metadata, operator, reason, tipo_vinculo=kind,
                        selecionado_previsao=selected, compatibilidade=compatibility)
                reverse_id = None
            session.flush()
            result = {"operacao_id": operation_id, "acao": acao,
                      "produto_ids": [product.id for product in products], "codigo_produto": code}
            session.add(OperacaoCatalogacao(id=operation_id, request_hash=digest, acao=acao,
                operador=operator, motivo=reason, antes_json=before,
                depois_json=[_snapshot(session, product) for product in products],
                resultado_json=result, reverte_id=reverse_id))
            session.flush()
            return result
    except CatalogacaoError:
        raise
    except EstoqueError:
        raise
    except IntegrityError:
        _error("MAPPING_CONFLICT", "Há conflito no catálogo. A operação inteira foi revertida; atualize a consulta.")
    except SQLAlchemyError:
        _error("STORAGE_ERROR", "Não foi possível persistir a catalogação. Nenhuma alteração foi confirmada.")
