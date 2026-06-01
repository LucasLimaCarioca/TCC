from app.database import db


class Produto(db.Model):

    __tablename__ = "produtos"

    id = db.Column(
        db.Integer,
        primary_key=True
    )

    nome = db.Column(
        db.String(100),
        nullable=False,
        unique=True
    )

    # Categoria representa o formato comercial vendido pela fábrica.
    # Exemplos: caixa de 10L, caixa de 5L, caixa de sundae.
    categoria = db.Column(
        db.String(100),
        nullable=False,
        default="sorvete simples"
    )

    # Sabor/variação do produto dentro da categoria.
    # Exemplos: chocolate, morango, caixa A.
    sabor = db.Column(
        db.String(100),
        nullable=False,
        default="tradicional"
    )

    preco = db.Column(
        db.Float,
        nullable=False
    )

    descricao = db.Column(
        db.String(255),
        nullable=True
    )

    # Estoque simplificado da fase 2.
    # A quantidade fica no proprio produto para manter o prototipo pequeno.
    quantidade_disponivel = db.Column(
        db.Integer,
        nullable=False,
        default=0
    )

    ativo = db.Column(
        db.Boolean,
        nullable=False,
        default=True
    )

    # Um produto pode aparecer em varias vendas.
    vendas = db.relationship(
        "Venda",
        back_populates="produto"
    )
