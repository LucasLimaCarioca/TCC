"""Seed isolado com bancos, catálogos e clientes exclusivamente fictícios."""

from datetime import datetime
from pathlib import Path
import runpy
import sqlite3
import subprocess

import pytest
from sqlalchemy import event

from app import app as app_module
from app.database import db
from app.models.cliente import Cliente
from app.models.movimentacao_estoque import MovimentacaoEstoque
from app.models.produto import Produto
from app.models.venda import Venda
from app.services import demonstracao_service as service


@pytest.fixture
def demo_project(tmp_path, monkeypatch):
    root = tmp_path / "projeto ficticio"
    root.mkdir()
    subprocess.run(["git", "init", "--quiet", str(root)], check=True, capture_output=True)
    (root / ".gitignore").write_text("/data/\n")
    directory = root / "data/artifacts"
    directory.mkdir(parents=True)
    operational = directory / "sorvetes.db"
    # Um arquivo sentinela prova que o seed sequer tenta abrir o banco padrão.
    operational.write_bytes(b"BANCO OPERACIONAL FICTICIO A PRESERVAR")
    monkeypatch.setattr(service, "PROJECT_ROOT", root)
    return root, operational


def snapshot(path):
    with sqlite3.connect(path) as connection:
        tables = sorted(row[0] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"))
        return {table: connection.execute(f'SELECT * FROM "{table}" ORDER BY 1').fetchall()
                for table in tables}


def close(application):
    with application.app_context():
        db.session.remove()
        db.engine.dispose()


def test_importar_seed_nao_abre_banco_nem_cria_aplicacao(demo_project, monkeypatch):
    root, operational = demo_project
    before = operational.read_bytes()
    monkeypatch.setattr(service, "_application", lambda *_: pytest.fail("Importar seed criou app"))
    module = runpy.run_path(str(Path(__file__).resolve().parents[1] / "seed.py"), run_name="seed_importado_ficticio")
    assert callable(module["main"])
    assert not (root / service.DATABASE_RELATIVE).exists()
    assert operational.read_bytes() == before


def test_cli_exige_opcao_demo_sem_abrir_banco(demo_project, monkeypatch, capsys):
    root, operational = demo_project
    monkeypatch.setattr(service, "_application", lambda *_: pytest.fail("CLI sem confirmação criou app"))
    module = runpy.run_path(str(Path(__file__).resolve().parents[1] / "seed.py"), run_name="seed_cli_ficticio")
    with pytest.raises(SystemExit) as error:
        module["main"]([])
    assert error.value.code == 2
    assert "--demo" in capsys.readouterr().err
    assert not (root / service.DATABASE_RELATIVE).exists()
    assert operational.read_bytes() == b"BANCO OPERACIONAL FICTICIO A PRESERVAR"


def test_seed_cria_somente_banco_demo_com_marcacao_e_entradas_auditaveis(demo_project, monkeypatch):
    root, operational = demo_project
    monkeypatch.setattr(app_module, "_database_uri", lambda: pytest.fail("Seed abriu URI operacional"))
    result = service.preparar_demonstracao()
    assert result["database"] == root / service.DATABASE_RELATIVE
    assert result["produtos_criados"] == 11 and result["clientes_criados"] == 5
    assert operational.read_bytes() == b"BANCO OPERACIONAL FICTICIO A PRESERVAR"
    assert result["database"].stat().st_mode & 0o777 == 0o600
    assert (root / service.MARKER_RELATIVE).stat().st_mode & 0o777 == 0o600
    application = service.create_demo_app()
    try:
        assert application.config["TCC_DEMO_MODE"] is True
        assert application.config["FORECAST_MANIFEST"] is None
        with application.app_context():
            assert Produto.query.count() == 11
            assert all(product.demonstracao and product.codigo_externo is None
                       for product in Produto.query.all())
            movements = MovimentacaoEstoque.query.all()
            assert len(movements) == 11
            assert all(m.tipo_movimentacao == "entrada" and m.saldo_anterior == 0
                       and m.quantidade == m.saldo_posterior == 20
                       and "demonstração" in m.motivo for m in movements)
            assert Cliente.query.count() == 5
    finally:
        close(application)


def test_reexecutar_seed_nao_redefine_saldos_precos_minimos_status_ou_clientes(demo_project):
    root, operational = demo_project
    result = service.preparar_demonstracao()
    application = service.create_demo_app()
    try:
        with application.app_context():
            product = Produto.query.order_by(Produto.id).first()
            product.preco = 987.65
            product.quantidade_disponivel = 7
            product.estoque_minimo = 3
            product.ativo = False
            product.descricao = "Descrição fictícia editada pelo operador."
            Cliente.query.order_by(Cliente.id).first().telefone = "000000000"
            db.session.commit()
    finally:
        close(application)
    before = snapshot(result["database"])
    second = service.preparar_demonstracao()
    assert second["produtos_criados"] == second["clientes_criados"] == 0
    assert snapshot(result["database"]) == before
    assert operational.read_bytes() == b"BANCO OPERACIONAL FICTICIO A PRESERVAR"


def test_banco_preexistente_sem_marcador_nao_e_adotado_ou_alterado(demo_project):
    root, operational = demo_project
    target = root / service.DATABASE_RELATIVE
    target.write_bytes(b"BANCO FICTICIO PREEXISTENTE SEM PROVA DE ORIGEM")
    with pytest.raises(service.DemonstracaoError, match="sem marcador"):
        service.preparar_demonstracao()
    assert target.read_bytes() == b"BANCO FICTICIO PREEXISTENTE SEM PROVA DE ORIGEM"
    assert not (root / service.MARKER_RELATIVE).exists()
    assert operational.read_bytes() == b"BANCO OPERACIONAL FICTICIO A PRESERVAR"


def test_marcador_invalido_nao_autoriza_mudar_banco_preexistente(demo_project):
    root, _ = demo_project
    result = service.preparar_demonstracao()
    before = snapshot(result["database"])
    (root / service.MARKER_RELATIVE).write_text('{"ambiente":"operacional"}')
    with pytest.raises(service.DemonstracaoError, match="Marcador"):
        service.preparar_demonstracao()
    assert snapshot(result["database"]) == before


@pytest.mark.parametrize("contamination", ["produto_sem_marca", "codigo_externo", "venda", "historico"])
def test_seed_preserva_dados_operacionais_ou_historicos_inseridos_no_demo(demo_project, contamination):
    root, operational = demo_project
    result = service.preparar_demonstracao()
    application = service.create_demo_app()
    try:
        with application.app_context():
            product = Produto.query.order_by(Produto.id).first()
            if contamination == "produto_sem_marca":
                db.session.add(Produto(nome="Produto operacional fictício", preco=333,
                    quantidade_disponivel=43, estoque_minimo=9, ativo=True))
            elif contamination == "codigo_externo":
                product.codigo_externo = "00042"
            elif contamination == "venda":
                db.session.add(Venda(produto_id=product.id, quantidade=1, valor_total=1,
                    cliente_nome="Cliente fictício", data_venda=datetime(2026, 1, 1)))
            else:
                from datetime import date
                from app.models.venda_historica import VendaHistorica
                db.session.add(VendaHistorica(codigo_produto="00042", data=date(2026, 1, 1),
                    quantidade=3, numero_pedidos=1, source_hash="a"*64, dataset_hash="b"*64))
            db.session.commit()
    finally:
        close(application)
    before = snapshot(result["database"])
    with pytest.raises(service.DemonstracaoError):
        service.preparar_demonstracao()
    assert snapshot(result["database"]) == before
    assert operational.read_bytes() == b"BANCO OPERACIONAL FICTICIO A PRESERVAR"


def test_falha_da_movimentacao_reverte_cadastro_aditivo_inteiro(demo_project, monkeypatch):
    _, operational = demo_project
    result = service.preparar_demonstracao()
    before = snapshot(result["database"])
    monkeypatch.setattr(service, "PRODUCTS", (*service.PRODUCTS,
        ("categoria fictícia nova", "sabor fictício novo", 77)))

    def fail(*_):
        raise RuntimeError("Falha fictícia de persistência.")

    event.listen(MovimentacaoEstoque, "before_insert", fail)
    try:
        with pytest.raises(service.DemonstracaoError, match="Falha na preparação"):
            service.preparar_demonstracao()
    finally:
        event.remove(MovimentacaoEstoque, "before_insert", fail)
    assert snapshot(result["database"]) == before
    assert operational.read_bytes() == b"BANCO OPERACIONAL FICTICIO A PRESERVAR"


def test_falha_em_banco_novo_remove_somente_artefatos_da_propria_tentativa(demo_project, monkeypatch):
    root, operational = demo_project

    def fail():
        raise RuntimeError("Falha fictícia da semeadura.")

    monkeypatch.setattr(service, "_seed_rows", fail)
    with pytest.raises(service.DemonstracaoError):
        service.preparar_demonstracao()
    assert not (root / service.DATABASE_RELATIVE).exists()
    assert not (root / service.MARKER_RELATIVE).exists()
    assert operational.read_bytes() == b"BANCO OPERACIONAL FICTICIO A PRESERVAR"


def test_factory_demo_nao_prepara_implicitamente_banco(demo_project):
    root, operational = demo_project
    with pytest.raises(service.DemonstracaoError, match="Prepare antes"):
        service.create_demo_app()
    assert not (root / service.DATABASE_RELATIVE).exists()
    assert operational.read_bytes() == b"BANCO OPERACIONAL FICTICIO A PRESERVAR"


def test_produto_novo_sem_marcacao_explicita_nao_vira_demonstracao(app):
    with app.app_context():
        product = Produto(nome="Produto comercial fictício", preco=20)
        db.session.add(product)
        db.session.commit()
        assert product.demonstracao is False


def test_semeadura_interna_recusa_app_operacional(app):
    with app.app_context(), pytest.raises(service.DemonstracaoError, match="ambiente separado"):
        service._seed_rows()
