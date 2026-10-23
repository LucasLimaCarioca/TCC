"""Comandos locais; conteúdo de previsões é exportado somente para data/."""

import json
import time
from uuid import uuid4

import click
from flask import current_app
from flask.cli import with_appcontext

from app.services.preparacao_dados_service import private_path, _write_private
from app.services.previsao_service import PrevisaoError, PrevisaoService


@click.group("previsao")
def forecast_commands():
    """Ativa artefatos e opera o Agente de Previsão local."""


@forecast_commands.command("carregar")
@click.option("--manifest", required=True, help="Manifesto concluído dentro de data/artifacts/.")
@with_appcontext
def load_command(manifest):
    """Valida e persiste uma execução, sem iniciar XMPP nem treinar."""
    try:
        PrevisaoService(current_app._get_current_object()).carregar(manifest)
    except PrevisaoError as error:
        raise click.ClickException(f"{error.code}: {error}") from None
    click.echo("Execução de previsão validada e persistida localmente.")


@forecast_commands.command("associar-produto")
@click.option("--produto-id", type=click.IntRange(min=1), required=True)
@click.option("--codigo-externo", required=True, help="Código textual confirmado, preservando zeros à esquerda.")
@with_appcontext
def map_command(produto_id, codigo_externo):
    """Registra uma associação fornecida pelo operador, sem inferir por nome."""
    try:
        PrevisaoService(current_app._get_current_object()).associar_produto(produto_id, codigo_externo)
    except PrevisaoError as error:
        raise click.ClickException(f"{error.code}: {error}") from None
    click.echo("Associação explícita registrada localmente.")


def _export(app, payload):
    root = PrevisaoService(app).root
    path = private_path(root, "data/reports/consulta-agente-" + uuid4().hex + ".json")
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    _write_private(path, json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
    click.echo("Resultado privado: " + path.relative_to(root).as_posix())


def _with_runtime(operation):
    from app.agents.base_agent import AgentUnavailable, RemoteFailure
    from app.agents.gateway import AgentGateway, start_runtime
    from app.services.estoque_service import EstoqueError

    app = current_app._get_current_object()
    runtime = None
    try:
        runtime = start_runtime(app)
        runtime.call(lambda: runtime.agents["previsao"].aguardar_inicializacao(),
                     timeout=runtime.config.startup_timeout)
        operation(app, AgentGateway(runtime))
    except (PrevisaoError, EstoqueError, RemoteFailure) as error:
        raise click.ClickException(f"{error.code}: {error}") from None
    except AgentUnavailable:
        raise click.ClickException("Runtime local indisponível; confira as senhas, portas e se a aplicação já está em execução.") from None
    except (ValueError, OSError):
        raise click.ClickException("Não foi possível configurar a operação ou gravar o resultado privado.") from None
    finally:
        if runtime is not None:
            runtime.stop()


@forecast_commands.command("consultar")
@click.option("--produto-id", type=click.IntRange(min=1), default=None, help="Omitir para consultar o total geral.")
@click.option("--granularidade", type=click.Choice(["diaria", "semanal", "mensal"]), default="diaria")
@click.option("--horizonte", type=click.IntRange(1, 366), default=7)
@with_appcontext
def query_command(produto_id, granularidade, horizonte):
    """Consulta Estoque → Previsão por XMPP e grava JSON privado."""
    def execute(app, gateway):
        _export(app, gateway.consultar_previsao(produto_id, granularidade, horizonte).payload)
    _with_runtime(execute)


@forecast_commands.command("sincronizar-alerta")
@click.option("--produto-id", type=click.IntRange(min=1), required=True)
@click.option("--horizonte", type=click.IntRange(1, 366), default=None,
              help="Horizonte em dias; omitir usa a configuração dos alertas preventivos.")
@with_appcontext
def synchronize_alert_command(produto_id, horizonte):
    """Reavalia o alerta preventivo por XMPP e grava o resultado privado."""
    def execute(app, gateway):
        _export(app, gateway.sincronizar_alerta_preventivo(produto_id, horizonte))
    _with_runtime(execute)


@forecast_commands.command("atualizar")
@click.option("--corte", required=True, help="Último dia completo aprovado, YYYY-MM-DD.")
@click.option("--horizonte", type=click.IntRange(1, 366), default=28)
@click.option("--limite-segundos", type=click.IntRange(1, 86400), default=3600,
              help="Prazo de acompanhamento do trabalho, sem alterar o timeout das consultas.")
@with_appcontext
def update_command(corte, horizonte, limite_segundos):
    """Solicita atualização XMPP e acompanha o trabalho fora do Inbox."""
    def execute(app, gateway):
        from app.services.previsao_service import _day
        _day(corte)
        response = gateway.atualizar_previsao(corte, horizonte).payload
        click.echo("Atualização recebida pelo agente; processamento local em segundo plano.")
        deadline = time.monotonic() + limite_segundos
        while time.monotonic() < deadline:
            status = gateway.status_previsao(response["job_id"]).payload
            if status["status"] == "concluido":
                _export(app, status)
                return
            if status["status"] == "falhou":
                raise PrevisaoError(status["erro"]["code"], status["erro"]["message"])
            time.sleep(0.5)
        raise PrevisaoError("UPDATE_TIMEOUT", "O prazo de acompanhamento terminou; o runtime será encerrado e solicitará a interrupção do trabalho.")
    _with_runtime(execute)
