"""Dados fictícios opt-in em armazenamento próprio, sem tocar o catálogo padrão."""

import json
import os
from pathlib import Path
import sqlite3

from flask import current_app
from sqlalchemy import func, select

from app.app import create_app
from app.database import db
from app.migrations import upgrade_database
from app.models.cliente import Cliente
from app.models.movimentacao_estoque import MovimentacaoEstoque
from app.models.produto import Produto
from app.services.estoque_service import _transacao
from app.services.preparacao_dados_service import PreparationError, private_path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATABASE_RELATIVE = "data/artifacts/demonstracao.db"
MARKER_RELATIVE = "data/artifacts/demonstracao.json"
MARKER = {"ambiente": "demonstracao_tcc", "versao": 1}

# Valores inventados do cenário de demonstração original, independentes do CSV.
PRODUCTS = (
    ("caixa de 10L", "morango", 101.0),
    ("caixa de 10L", "chocolate", 102.0),
    ("caixa de 10L", "baunilha", 100.0),
    ("caixa de 5L", "morango", 66.0),
    ("caixa de 5L", "chocolate", 67.0),
    ("caixa de 5L", "baunilha", 65.0),
    ("caixa de sundae", "morango", 45.0),
    ("caixa de sundae", "chocolate", 48.0),
    ("caixa de picole", "caixa A", 120.0),
    ("caixa de picole", "caixa B", 120.0),
    ("caixa de picole", "caixa C", 120.0),
)


class DemonstracaoError(ValueError):
    """Mensagem segura: não inclui linhas, nomes ou valores do banco inspecionado."""


def _paths(project_root=None):
    root = Path(project_root or PROJECT_ROOT).resolve()
    try:
        return root, private_path(root, DATABASE_RELATIVE), private_path(root, MARKER_RELATIVE)
    except PreparationError:
        raise DemonstracaoError("O banco de demonstração deve permanecer privado em data/artifacts/.") from None


def _exclusive_regular(path):
    if path.is_symlink() or not path.is_file() or path.stat().st_nlink != 1:
        raise DemonstracaoError("O ambiente de demonstração contém um arquivo não exclusivo ou inválido.")


def _marker(marker):
    _exclusive_regular(marker)
    try:
        if json.loads(marker.read_text(encoding="utf-8")) != MARKER:
            raise ValueError
    except (ValueError, OSError, UnicodeError):
        raise DemonstracaoError("Marcador do ambiente de demonstração inválido.") from None


def _validate_readonly(database, *, seed):
    """Recusa um banco não dedicado antes de migração, backup ou qualquer escrita."""
    _exclusive_regular(database)
    try:
        with sqlite3.connect(database.as_uri() + "?mode=ro", uri=True) as connection:
            tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if "produtos" not in tables:
                raise DemonstracaoError("Banco de demonstração sem schema preparado.")
            columns = {row[1] for row in connection.execute("PRAGMA table_info(produtos)")}
            if not {"demonstracao", "codigo_externo"} <= columns:
                raise DemonstracaoError("O banco preexistente não possui identificação de demonstração.")
            if connection.execute(
                    "SELECT 1 FROM produtos WHERE demonstracao IS NOT 1 OR codigo_externo IS NOT NULL LIMIT 1"
            ).fetchone():
                raise DemonstracaoError("Há produtos sem identificação de demonstração ou associados ao histórico; banco preservado.")
            forbidden = ("vendas_historicas", "previsoes_execucoes", "previsoes_demanda")
            if seed:
                forbidden += ("vendas",)
            for table in forbidden:
                if table in tables and connection.execute(f'SELECT 1 FROM "{table}" LIMIT 1').fetchone():
                    raise DemonstracaoError("Há vendas ou dados históricos/de previsão; banco preservado sem semeadura.")
    except DemonstracaoError:
        raise
    except (sqlite3.Error, OSError):
        raise DemonstracaoError("Não foi possível validar o banco dedicado de demonstração.") from None


def _application(root, database):
    # URI explícita impede create_app de preparar/abrir sorvetes.db.
    return create_app({
        "SQLALCHEMY_DATABASE_URI": f"sqlite:///{database}",
        "TCC_DEMO_MODE": True,
        "FORECAST_PROJECT_ROOT": str(root),
        "FORECAST_MANIFEST": None,
        "FORECAST_REVIEW": None,
        "FORECAST_CALENDAR_OVERRIDES": None,
        "PREDICTIVE_ALERTS_AUTO_ENABLED": False,
    })


def create_demo_app(project_root=None):
    """Abre somente o ambiente demo já preparado; não cria nem semeia dados."""
    root, database, marker = _paths(project_root)
    if not database.exists() or not marker.exists():
        raise DemonstracaoError("Prepare antes o ambiente separado com python seed.py --demo.")
    _marker(marker)
    _validate_readonly(database, seed=False)
    return _application(root, database)


def _seed_rows():
    if current_app.config.get("TCC_DEMO_MODE") is not True:
        raise DemonstracaoError("A semeadura exige o ambiente separado de demonstração.")
    products_created = clients_created = 0
    with _transacao() as session:
        # Repetir a validação sob o mesmo bloqueio de escrita evita misturar um
        # cadastro operacional que tenha sido inserido entre a leitura e o seed.
        if session.scalar(select(Produto.id).where(
                (Produto.demonstracao.is_not(True)) | Produto.codigo_externo.is_not(None)).limit(1)):
            raise DemonstracaoError("Há produtos sem identificação de demonstração; banco preservado.")
        from app.models.venda import Venda
        from app.models.venda_historica import VendaHistorica
        from app.models.previsao_execucao import PrevisaoExecucao
        from app.models.previsao_demanda import PrevisaoDemanda
        for model in (Venda, VendaHistorica, PrevisaoExecucao, PrevisaoDemanda):
            if session.scalar(select(func.count()).select_from(model)):
                raise DemonstracaoError("Há vendas ou dados históricos/de previsão; banco preservado sem semeadura.")
        for category, flavor, price in PRODUCTS:
            name = f"{category} - {flavor}"
            product = session.scalar(select(Produto).where(Produto.nome == name))
            if product is not None:
                # Reexecução não redefine preço, saldo, mínimo, status ou descrição.
                continue
            product = Produto(nome=name, categoria=category, sabor=flavor, preco=price,
                descricao=f"Produto fictício de demonstração: {category}, sabor {flavor}.",
                quantidade_disponivel=20, estoque_minimo=0, ativo=True, demonstracao=True)
            session.add(product)
            session.flush()
            session.add(MovimentacaoEstoque(tipo_item="produto", produto_id=product.id,
                tipo_movimentacao="entrada", quantidade=20, saldo_anterior=0,
                saldo_posterior=20, motivo="Cadastro inicial de demonstração (dados fictícios)."))
            products_created += 1
        for index in range(1, 6):
            name = "Cliente Simulado" if index == 1 else f"Cliente Simulado {index}"
            if session.scalar(select(Cliente.id).where(Cliente.nome == name).limit(1)) is None:
                session.add(Cliente(nome=name))
                clients_created += 1
    return products_created, clients_created


def preparar_demonstracao(project_root=None):
    """Opt-in aditivo e exclusivo: jamais abre o banco operacional padrão."""
    root, database, marker = _paths(project_root)
    created_database = created_marker = False
    succeeded = False
    application = None
    try:
        if database.exists():
            if not marker.exists():
                raise DemonstracaoError("Banco preexistente sem marcador de demonstração; nenhuma alteração realizada.")
            _marker(marker)
            _validate_readonly(database, seed=True)
        else:
            if marker.exists():
                raise DemonstracaoError("Marcador sem banco: revise o ambiente antes de recriá-lo.")
            for directory in (root / "data", root / "data/artifacts"):
                directory.mkdir(mode=0o700, exist_ok=True)
            descriptor = os.open(database, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            os.close(descriptor)
            created_database = True
            descriptor = os.open(marker, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                handle.write(json.dumps(MARKER) + "\n")
            created_marker = True
        application = _application(root, database)
        with application.app_context():
            upgrade_database(project_root=root)
            products, clients = _seed_rows()
        succeeded = True
        return {"database": database, "produtos_criados": products, "clientes_criados": clients}
    except DemonstracaoError:
        raise
    except Exception:
        raise DemonstracaoError("Falha na preparação; o banco operacional não foi acessado.") from None
    finally:
        if application is not None:
            with application.app_context():
                db.session.remove()
                db.engine.dispose()
        # Somente artefatos novos desta própria tentativa são retirados em caso
        # de falha; um banco preexistente jamais é excluído nem substituído.
        if not succeeded:
            if created_marker:
                marker.unlink(missing_ok=True)
            if created_database:
                database.unlink(missing_ok=True)
