"""Autoridade transacional de estoque; nunca abate matéria-prima por venda."""

from contextlib import contextmanager
from decimal import Decimal, InvalidOperation
import hashlib
import json
from uuid import UUID, uuid4

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.database import db
from app.models.alerta_estoque import AlertaEstoque
from app.models.materia_prima import MateriaPrima
from app.models.movimentacao_estoque import MovimentacaoEstoque
from app.models.operacao_estoque import OperacaoEstoque
from app.models.produto import Produto
from app.models.timestamps import utc_now
from app.models.venda import Venda


class EstoqueError(ValueError):
    def __init__(self, code, message):
        self.code = code
        super().__init__(message)


def inteiro(value, minimum=1):
    if type(value) is not int or not minimum <= value <= 2**63 - 1:
        raise EstoqueError("INVALID_QUANTITY", "Informe uma quantidade inteira válida.")
    return value


def quantidade_item(value, tipo_item, minimum=0):
    if tipo_item == "produto":
        return inteiro(value, minimum)
    if tipo_item != "materia_prima" or type(value) not in (int, str, Decimal):
        raise EstoqueError("INVALID_QUANTITY", "Informe uma quantidade decimal válida.")
    try:
        amount = Decimal(value)
        if (not amount.is_finite() or amount < minimum or amount >= Decimal("1e16") or
                amount != amount.quantize(Decimal("0.000000001"))):
            raise InvalidOperation
    except (InvalidOperation, ValueError):
        raise EstoqueError("INVALID_QUANTITY", "Use uma quantidade não negativa com até nove casas decimais.") from None
    return amount


def agrupar_itens(itens):
    if type(itens) is not list:
        raise EstoqueError("INVALID_INPUT", "Pedido inválido.")
    if not itens:
        raise EstoqueError("INVALID_INPUT", "Informe ao menos um produto no pedido.")
    grouped = {}
    for item in itens:
        if type(item) is not dict:
            raise EstoqueError("INVALID_INPUT", "Pedido inválido.")
        produto_id = inteiro(item.get("produto_id"))
        quantidade = item.get("quantidade")
        if type(quantidade) is not int or quantidade <= 0:
            raise EstoqueError("INVALID_QUANTITY", "Informe uma quantidade maior que zero.")
        grouped[produto_id] = inteiro(grouped.get(produto_id, 0) + quantidade)
    return [{"produto_id": key, "quantidade": value} for key, value in grouped.items()]


def validar_operacao_id(operacao_id):
    try:
        return str(UUID(operacao_id)) if operacao_id is not None else str(uuid4())
    except (ValueError, TypeError, AttributeError):
        raise EstoqueError("INVALID_OPERATION", "A chave da operação deve ser um UUID válido.") from None


def validar_cliente(cliente_nome):
    if not isinstance(cliente_nome, str):
        raise EstoqueError("INVALID_INPUT", "Nome do cliente inválido.")
    nome = cliente_nome.strip()
    if not nome or len(nome) > 100:
        raise EstoqueError("INVALID_INPUT", "Nome do cliente inválido.")
    return nome


def resolver_chaves_operacao(chave_header, operacao_id):
    chaves = [validar_operacao_id(value) for value in (chave_header, operacao_id) if value is not None]
    if len(set(chaves)) > 1:
        raise EstoqueError("INVALID_OPERATION", "Idempotency-Key e operacao_id devem identificar a mesma operação.")
    return chaves[0] if chaves else None


def _decimal_canonico(value):
    if type(value) is int:
        return str(value)
    if value == 0:
        return "0"
    formatted = format(value, "f")
    return formatted.rstrip("0").rstrip(".") if "." in formatted else formatted


@contextmanager
def _transacao():
    # Sessão própria: nenhuma transação de leitura do chat atravessa o worker.
    # Reservar a escrita ANTES de ler os saldos impede duas vendas concorrentes
    # de aprovarem o mesmo estoque no SQLite.
    with Session(db.engine, expire_on_commit=False) as session:
        try:
            session.execute(text("BEGIN IMMEDIATE"))
            yield session
            session.commit()
        except BaseException:
            session.rollback()
            raise
    db.session.expire_all()


def _item(session, tipo_item, item_id):
    model = {"produto": Produto, "materia_prima": MateriaPrima}.get(tipo_item)
    if model is None:
        raise EstoqueError("INVALID_ITEM", "Tipo de item inválido.")
    item = session.get(model, inteiro(item_id))
    if item is None or not item.ativo:
        message = "Produto não encontrado." if tipo_item == "produto" else "Matéria-prima não encontrada."
        raise EstoqueError("PRODUCT_NOT_FOUND" if tipo_item == "produto" else "ITEM_NOT_FOUND", message)
    return item


def _referencia(tipo_item, item_id):
    return {"tipo_item": tipo_item, "produto_id": item_id if tipo_item == "produto" else None,
            "materia_prima_id": item_id if tipo_item == "materia_prima" else None}


def _atualizar_alerta(session, tipo_item, item):
    field = AlertaEstoque.produto_id if tipo_item == "produto" else AlertaEstoque.materia_prima_id
    alerts = session.scalars(select(AlertaEstoque).where(
        AlertaEstoque.tipo_item == tipo_item, field == item.id,
        AlertaEstoque.tipo_alerta == "estoque_minimo", AlertaEstoque.ativo.is_(True)
    ).order_by(AlertaEstoque.id)).all()
    low = item.ativo and item.quantidade_disponivel <= item.estoque_minimo
    message = f"{item.nome}: saldo {item.quantidade_disponivel}, mínimo {item.estoque_minimo}."
    if low and not alerts:
        session.add(AlertaEstoque(**_referencia(tipo_item, item.id), tipo_alerta="estoque_minimo",
                                 mensagem=message, nivel="aviso", ativo=True))
    elif low:
        alerts[0].mensagem = message
    # Resolve recuperação e eventuais duplicatas legadas, preservando o histórico.
    for alert in alerts[1:] if low else alerts:
        alert.ativo = False
        alert.resolvido_em = utc_now()


def _operacao(tipo, dados, operacao_id, execute):
    operation = validar_operacao_id(operacao_id)
    digest = hashlib.sha256(json.dumps({"tipo": tipo, "dados": dados}, sort_keys=True,
                                      ensure_ascii=False, allow_nan=False).encode()).hexdigest()
    with _transacao() as session:
        previous = session.get(OperacaoEstoque, operation)
        if previous:
            if previous.request_hash != digest or previous.tipo != tipo:
                raise EstoqueError("IDEMPOTENCY_CONFLICT", "Chave de operação já usada para outra solicitação.")
            return previous.resultado
        result = execute(session)
        session.add(OperacaoEstoque(id=operation, tipo=tipo, request_hash=digest, resultado=result))
        return result


def consultar_disponibilidade(itens):
    grouped = agrupar_itens(itens)
    result = []
    with Session(db.engine) as session:
        for entry in grouped:
            produto = _item(session, "produto", entry["produto_id"])
            result.append({"produto_id": produto.id, "solicitado": entry["quantidade"],
                           "disponivel": produto.quantidade_disponivel,
                           "ok": produto.quantidade_disponivel >= entry["quantidade"]})
    return {"available": all(row["ok"] for row in result), "items": result}


def consultar_produto(produto_id):
    with Session(db.engine) as session:
        produto = _item(session, "produto", produto_id)
        return {"produto_id": produto.id, "disponivel": produto.quantidade_disponivel,
                "estoque_minimo": produto.estoque_minimo}


def registrar_venda(itens, cliente_nome="Cliente Simulado", operacao_id=None):
    grouped = agrupar_itens(itens)
    cliente_nome = validar_cliente(cliente_nome)

    def execute(session):
        products = {}
        for entry in grouped:
            produto = _item(session, "produto", entry["produto_id"])
            quantidade = entry["quantidade"]
            if produto.quantidade_disponivel < quantidade:
                message = (f"No momento não temos {produto.nome} em estoque." if produto.quantidade_disponivel == 0
                           else f"No momento temos apenas {produto.quantidade_disponivel} unidades de {produto.nome}.")
                raise EstoqueError("INSUFFICIENT_STOCK", message)
            products[produto.id] = produto
        vendas, movements = [], []
        for entry in grouped:
            produto = products[entry["produto_id"]]
            before = produto.quantidade_disponivel
            sale = Venda(cliente_nome=cliente_nome, produto_id=produto.id, quantidade=entry["quantidade"],
                         valor_total=produto.preco * entry["quantidade"])
            session.add(sale)
            session.flush()
            produto.quantidade_disponivel -= entry["quantidade"]
            movement = MovimentacaoEstoque(**_referencia("produto", produto.id), venda_id=sale.id,
                tipo_movimentacao="venda", quantidade=entry["quantidade"], saldo_anterior=before,
                saldo_posterior=produto.quantidade_disponivel, motivo=f"Venda {sale.id} confirmada.")
            session.add(movement)
            _atualizar_alerta(session, "produto", produto)
            session.flush()
            vendas.append(sale.id)
            movements.append(movement.id)
        return {"success": True, "vendas": vendas, "movimentacoes": movements}

    return _operacao("venda", {"items": sorted(grouped, key=lambda item: item["produto_id"]),
                              "cliente_nome": cliente_nome}, operacao_id, execute)


def movimentar(tipo_item, item_id, tipo_movimentacao, quantidade, motivo, operacao_id=None):
    if not isinstance(tipo_movimentacao, str) or tipo_movimentacao not in {"entrada", "saida", "ajuste"}:
        raise EstoqueError("INVALID_INPUT", "Use entrada, saída ou ajuste manual.")
    if not isinstance(motivo, str) or not motivo.strip() or len(motivo) > 500:
        raise EstoqueError("INVALID_INPUT", "Informe um motivo de até 500 caracteres.")
    item_id = inteiro(item_id)
    amount = quantidade_item(quantidade, tipo_item)
    if tipo_movimentacao != "ajuste" and amount <= 0:
        raise EstoqueError("INVALID_QUANTITY", "Informe uma quantidade maior que zero.")

    def execute(session):
        item = _item(session, tipo_item, item_id)
        before = item.quantidade_disponivel
        after = amount if tipo_movimentacao == "ajuste" else before + (amount if tipo_movimentacao == "entrada" else -amount)
        if after < 0:
            raise EstoqueError("INSUFFICIENT_STOCK", "Saldo insuficiente para a saída solicitada.")
        quantidade_item(after, tipo_item)
        delta = abs(after - before)
        if not delta:
            return {"alterado": False, "movimentacao_id": None, "saldo": str(after)}
        item.quantidade_disponivel = after
        movement = MovimentacaoEstoque(**_referencia(tipo_item, item_id), tipo_movimentacao=tipo_movimentacao,
            quantidade=delta, saldo_anterior=before, saldo_posterior=after, motivo=motivo.strip())
        session.add(movement)
        _atualizar_alerta(session, tipo_item, item)
        session.flush()
        return {"alterado": True, "movimentacao_id": movement.id, "saldo": str(after)}

    return _operacao("movimentacao", {"tipo_item": tipo_item, "item_id": item_id,
        "tipo_movimentacao": tipo_movimentacao, "quantidade": _decimal_canonico(amount), "motivo": motivo.strip()}, operacao_id, execute)


def configurar_minimo(tipo_item, item_id, estoque_minimo):
    minimum = quantidade_item(estoque_minimo, tipo_item)
    with _transacao() as session:
        item = _item(session, tipo_item, item_id)
        item.estoque_minimo = minimum
        _atualizar_alerta(session, tipo_item, item)
    return {"estoque_minimo": str(minimum)}


def cadastrar_materia_prima(codigo, nome, unidade_medida, estoque_minimo="0"):
    codigo = codigo.strip().upper() if isinstance(codigo, str) else codigo
    nome = nome.strip() if isinstance(nome, str) else nome
    unidade_medida = unidade_medida.strip() if isinstance(unidade_medida, str) else unidade_medida
    for value, limit in [(codigo, 64), (nome, 100), (unidade_medida, 20)]:
        if not isinstance(value, str) or not value.strip() or len(value) > limit:
            raise EstoqueError("INVALID_INPUT", "Informe código, nome e unidade de medida válidos.")
    minimum = quantidade_item(estoque_minimo, "materia_prima")
    with _transacao() as session:
        # Reconhece códigos equivalentes também nos cadastros legados, sem
        # renomeá-los ou colidir com referências já existentes.
        if any(existing.strip().upper() == codigo for existing in session.scalars(select(MateriaPrima.codigo))):
            raise EstoqueError("DUPLICATE_MATERIAL", "Código de matéria-prima já cadastrado.")
        material = MateriaPrima(codigo=codigo, nome=nome, unidade_medida=unidade_medida,
                               quantidade_disponivel=Decimal(0), estoque_minimo=minimum)
        session.add(material)
        session.flush()
        _atualizar_alerta(session, "materia_prima", material)
        return {"id": material.id}


def sincronizar_alertas():
    """Reconciliação explícita dos itens legados, separada das consultas GET."""
    with _transacao() as session:
        checked = 0
        for tipo, model in [("produto", Produto), ("materia_prima", MateriaPrima)]:
            for item in session.scalars(select(model)).all():
                _atualizar_alerta(session, tipo, item)
                checked += 1
        return {"itens_verificados": checked}


def visao_estoque():
    # Sessão exclusiva de leitura: consultas não geram/resolvem alertas,
    # não reservam a escrita nem atualizam os timestamps existentes.
    with Session(db.engine) as session:
        products = session.scalars(select(Produto).where(Produto.ativo.is_(True)).order_by(Produto.nome)).all()
        materials = session.scalars(select(MateriaPrima).where(MateriaPrima.ativo.is_(True)).order_by(MateriaPrima.nome)).all()
        alerts = session.scalars(select(AlertaEstoque).order_by(AlertaEstoque.id.desc())).all()
        movements = session.scalars(select(MovimentacaoEstoque).order_by(MovimentacaoEstoque.id.desc()).limit(100)).all()
        return {
            "produtos": [{"produto_id": p.id, "nome": p.nome, "categoria": p.categoria, "sabor": p.sabor,
                          "quantidade_disponivel": p.quantidade_disponivel, "estoque_minimo": p.estoque_minimo} for p in products],
            "materias_primas": [{"id": m.id, "codigo": m.codigo, "nome": m.nome, "unidade_medida": m.unidade_medida,
                                "quantidade_disponivel": str(m.quantidade_disponivel), "estoque_minimo": str(m.estoque_minimo)} for m in materials],
            "alertas": [{"id": a.id, "tipo_item": a.tipo_item, "item_id": a.produto_id or a.materia_prima_id,
                         "tipo_alerta": a.tipo_alerta, "mensagem": a.mensagem, "nivel": a.nivel, "ativo": a.ativo,
                         "criado_em": a.criado_em.isoformat(),
                         "resolvido_em": a.resolvido_em.isoformat() if a.resolvido_em else None} for a in alerts],
            "movimentacoes": [{"id": m.id, "tipo_item": m.tipo_item, "item_id": m.produto_id or m.materia_prima_id,
                               "nome": (m.produto if m.tipo_item == "produto" else m.materia_prima).nome,
                               "tipo_movimentacao": m.tipo_movimentacao, "quantidade": str(m.quantidade),
                               "saldo_anterior": str(m.saldo_anterior), "saldo_posterior": str(m.saldo_posterior),
                               "venda_id": m.venda_id, "motivo": m.motivo, "criado_em": m.criado_em.isoformat()} for m in movements],
        }
