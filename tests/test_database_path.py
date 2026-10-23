"""Caminhos de banco em repositórios temporários, sem acessar dados locais reais."""

import sqlite3
import stat
import subprocess

import pytest

import app.app as app_module
from app.database import db
from app.services.preparacao_dados_service import PreparationError


@pytest.fixture
def project(tmp_path, monkeypatch):
    root = tmp_path / "projeto ficticio"
    root.mkdir()
    subprocess.run(["git", "init", "--quiet", str(root)], check=True, capture_output=True)
    (root / ".gitignore").write_text("/data/\n", encoding="utf-8")
    monkeypatch.setattr(app_module, "PROJECT_ROOT", root)
    return root


@pytest.fixture
def factory():
    applications = []

    def create(config=None):
        application = app_module.create_app(config)
        applications.append(application)
        return application

    yield create
    for application in applications:
        with application.app_context():
            db.session.remove()
            db.engine.dispose()


def test_banco_padrao_absoluto_independe_do_diretorio_atual(project, tmp_path, monkeypatch, factory):
    current = tmp_path / "outro diretorio"
    current.mkdir()
    monkeypatch.chdir(current)

    application = factory()
    expected = project / "data/artifacts/sorvetes.db"
    assert application.config["SQLALCHEMY_DATABASE_URI"] == f"sqlite:///{expected}"
    with application.app_context():
        assert db.engine.url.database == str(expected)
        db.create_all()
    assert expected.is_file()
    assert not (current / "data").exists()
    assert not (project / "instance").exists()


def test_novos_diretorios_e_banco_tem_permissoes_privadas(project, factory):
    factory()
    assert stat.S_IMODE((project / "data").stat().st_mode) == 0o700
    assert stat.S_IMODE((project / "data/artifacts").stat().st_mode) == 0o700
    assert stat.S_IMODE((project / "data/artifacts/sorvetes.db").stat().st_mode) == 0o600


@pytest.mark.parametrize("testing", [False, True])
def test_uri_explicita_nao_prepara_banco_padrao(project, tmp_path, factory, testing):
    target = tmp_path / "banco-ficticio.db"
    application = factory({
        "TESTING": testing,
        "SQLALCHEMY_DATABASE_URI": f"sqlite:///{target}",
    })
    with application.app_context():
        db.create_all()
    assert target.exists()
    assert not (project / "data").exists()
    assert not (project / "instance").exists()


def test_testing_sem_uri_usa_memoria_e_nao_toca_legado(project, factory):
    legacy = project / "instance/sorvetes.db"
    legacy.parent.mkdir()
    legacy.write_bytes(b"banco legado ficticio intocado")
    application = factory({"TESTING": True})
    with application.app_context():
        assert db.engine.url.database == ":memory:"
        db.create_all()
    assert legacy.read_bytes() == b"banco legado ficticio intocado"
    assert not (project / "data").exists()


def test_apenas_banco_legado_bloqueia_criacao_de_banco_vazio(project, factory):
    legacy = project / "instance/sorvetes.db"
    legacy.parent.mkdir()
    legacy.write_bytes(b"banco legado ficticio intocado")

    with pytest.raises(RuntimeError, match="Transfira-o com backup"):
        factory()

    assert legacy.read_bytes() == b"banco legado ficticio intocado"
    assert not (project / "data").exists()


def test_banco_privado_existente_e_usado_sem_recriar_ou_alterar_legado(project, factory):
    target = project / "data/artifacts/sorvetes.db"
    target.parent.mkdir(parents=True)
    with sqlite3.connect(target) as connection:
        connection.execute("CREATE TABLE exemplo (nome TEXT)")
        connection.execute("INSERT INTO exemplo VALUES ('registro ficticio')")
    legacy = project / "instance/sorvetes.db"
    legacy.parent.mkdir()
    legacy.write_bytes(b"legado ficticio")

    application = factory()
    with application.app_context(), db.engine.connect() as connection:
        assert connection.exec_driver_sql("SELECT nome FROM exemplo").scalar_one() == "registro ficticio"
    assert legacy.read_bytes() == b"legado ficticio"


def test_banco_padrao_exige_protecao_git_antes_de_criar_dados(project, factory):
    (project / ".gitignore").write_text("", encoding="utf-8")
    with pytest.raises(PreparationError, match="ignorados e fora do índice"):
        factory()
    assert not (project / "data").exists()


def test_banco_padrao_rejeita_link_simbolico(project, tmp_path, factory):
    other = tmp_path / "fora do repositorio"
    other.mkdir()
    (project / "data").symlink_to(other, target_is_directory=True)
    with pytest.raises(PreparationError, match="Links simbólicos"):
        factory()
    assert list(other.iterdir()) == []
