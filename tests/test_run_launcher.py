"""Launcher isolado: configuração fictícia, sem banco ou runtime XMPP real."""

import atexit
import os
from pathlib import Path
import runpy
from types import SimpleNamespace

import pytest

from app import app as app_module
from app.agents import gateway


CONFIG_KEYS = (
    "XMPP_ATENDIMENTO_PASSWORD", "XMPP_ESTOQUE_PASSWORD", "XMPP_PREVISAO_PASSWORD",
    "TCC_SPADE_ENABLED", "FLASK_DEBUG", "LAUNCHER_TEST_FICTICIO",
)


@pytest.fixture
def launcher(tmp_path, monkeypatch):
    directory = tmp_path / "projeto ficticio"
    directory.mkdir()
    path = directory / "run.py"
    path.write_text((Path(__file__).resolve().parents[1] / "run.py").read_text(), encoding="utf-8")
    other_directory = tmp_path / "outro diretorio ficticio"
    other_directory.mkdir()
    monkeypatch.chdir(other_directory)
    for key in (*CONFIG_KEYS, "PYTHON_DOTENV_DISABLED"):
        monkeypatch.delenv(key, raising=False)

    events = []
    registrations = []

    def snapshot():
        return {key: os.environ.get(key) for key in CONFIG_KEYS}

    class FakeApp:
        failure = False

        def run(self, **kwargs):
            events.append(("run", kwargs))
            if self.failure:
                raise RuntimeError("Falha fictícia do servidor Flask.")

    class FakeRuntime:
        def stop(self):
            events.append(("stop",))

    application = FakeApp()
    runtime = FakeRuntime()

    def create_app():
        events.append(("create_app", snapshot()))
        return application

    def start_runtime(app):
        assert app is application
        events.append(("start_runtime", snapshot()))
        return runtime

    monkeypatch.setattr(app_module, "create_app", create_app)
    monkeypatch.setattr(gateway, "start_runtime", start_runtime)
    monkeypatch.setattr(atexit, "register", registrations.append)

    def execute(name="__main__"):
        return runpy.run_path(str(path), run_name=name)

    yield SimpleNamespace(directory=directory, cwd=other_directory, path=path,
        execute=execute, events=events, registrations=registrations,
        app=application, runtime=runtime, snapshot=snapshot)
    # load_dotenv escreve diretamente no ambiente; remover essas entradas antes
    # de monkeypatch restaurar as variáveis preexistentes também isola os testes.
    for key in CONFIG_KEYS:
        os.environ.pop(key, None)


def write_environment(directory, **values):
    (directory / ".env").write_text(
        "".join(f"{key}={value}\n" for key, value in values.items()), encoding="utf-8")


def test_env_do_launcher_e_carregado_antes_do_factory_e_runtime_mesmo_com_outro_cwd(launcher):
    values = {
        "XMPP_ATENDIMENTO_PASSWORD": "senha-ficticia-atendimento",
        "XMPP_ESTOQUE_PASSWORD": "senha-ficticia-estoque",
        "XMPP_PREVISAO_PASSWORD": "senha-ficticia-previsao",
        "LAUNCHER_TEST_FICTICIO": "origem-launcher",
    }
    write_environment(launcher.directory, **values)
    write_environment(launcher.cwd, LAUNCHER_TEST_FICTICIO="origem-cwd",
        XMPP_ATENDIMENTO_PASSWORD="senha-ficticia-cwd")

    result = launcher.execute()

    assert result["app"] is launcher.app
    assert [event[0] for event in launcher.events] == ["create_app", "start_runtime", "run", "stop"]
    for event in launcher.events[:2]:
        assert all(event[1][key] == value for key, value in values.items())
    assert launcher.events[2][1] == {"debug": False, "use_reloader": False, "load_dotenv": False}


def test_variaveis_exportadas_prevalecem_sobre_env_do_launcher(launcher, monkeypatch):
    write_environment(launcher.directory,
        XMPP_ATENDIMENTO_PASSWORD="senha-ficticia-arquivo",
        XMPP_ESTOQUE_PASSWORD="senha-ficticia-arquivo",
        XMPP_PREVISAO_PASSWORD="senha-ficticia-arquivo",
        TCC_SPADE_ENABLED="0", FLASK_DEBUG="1")
    exported = {
        "XMPP_ATENDIMENTO_PASSWORD": "senha-ficticia-exportada-atendimento",
        "XMPP_ESTOQUE_PASSWORD": "senha-ficticia-exportada-estoque",
        "XMPP_PREVISAO_PASSWORD": "senha-ficticia-exportada-previsao",
        "TCC_SPADE_ENABLED": "1", "FLASK_DEBUG": "0",
    }
    for key, value in exported.items():
        monkeypatch.setenv(key, value)

    launcher.execute()

    for event in launcher.events[:2]:
        assert all(event[1][key] == value for key, value in exported.items())
    assert launcher.events[2][1]["debug"] is False


def test_modo_local_configurado_no_env_nao_inicia_nem_registra_runtime(launcher):
    write_environment(launcher.directory, TCC_SPADE_ENABLED="0", FLASK_DEBUG="1")

    launcher.execute()

    assert [event[0] for event in launcher.events] == ["create_app", "run"]
    assert launcher.events[0][1]["TCC_SPADE_ENABLED"] == "0"
    assert launcher.events[1][1] == {"debug": True, "use_reloader": False, "load_dotenv": False}
    assert launcher.registrations == []


def test_importar_launcher_nao_carrega_env_nem_inicia_runtime_ou_servidor(launcher):
    write_environment(launcher.directory,
        LAUNCHER_TEST_FICTICIO="nao-deve-ser-carregado", TCC_SPADE_ENABLED="1")
    write_environment(launcher.cwd, LAUNCHER_TEST_FICTICIO="tambem-nao-carregar")

    result = launcher.execute("launcher_importado_ficticio")

    assert result["app"] is launcher.app
    assert [event[0] for event in launcher.events] == ["create_app"]
    assert all(value is None for value in launcher.events[0][1].values())
    assert launcher.registrations == []
    assert all(value is None for value in launcher.snapshot().values())


@pytest.mark.parametrize("failure", [False, True])
def test_runtime_e_encerrado_quando_servidor_retorna_ou_falha(launcher, failure):
    write_environment(launcher.directory, TCC_SPADE_ENABLED="1")
    launcher.app.failure = failure

    if failure:
        with pytest.raises(RuntimeError, match="Falha fictícia do servidor Flask"):
            launcher.execute()
    else:
        launcher.execute()

    assert [event[0] for event in launcher.events] == ["create_app", "start_runtime", "run", "stop"]
    assert launcher.registrations == [launcher.runtime.stop]


def test_sem_env_do_launcher_nao_descobre_env_do_cwd(launcher, monkeypatch):
    write_environment(launcher.cwd,
        LAUNCHER_TEST_FICTICIO="nao-carregar-cwd", FLASK_DEBUG="1", TCC_SPADE_ENABLED="1")
    monkeypatch.setenv("TCC_SPADE_ENABLED", "0")

    launcher.execute()

    assert launcher.events[0][1]["LAUNCHER_TEST_FICTICIO"] is None
    assert launcher.events[0][1]["FLASK_DEBUG"] is None
    assert launcher.events[1][1] == {"debug": False, "use_reloader": False, "load_dotenv": False}
    assert launcher.snapshot()["LAUNCHER_TEST_FICTICIO"] is None
