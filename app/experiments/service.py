"""Orquestra a validação sem publicar previsões ou abrir o banco operacional."""

from datetime import datetime, timezone
from hashlib import sha256
from importlib.metadata import PackageNotFoundError, version
import math
from pathlib import Path
import platform
import subprocess
from time import monotonic
from uuid import uuid4

from app.experiments.errors import ExperimentError
from app.experiments.forecast_evidence import collect_forecast_evidence
from app.experiments.functional import run_functional_experiment
from app.experiments.reporting import export_reports
from app.services.preparacao_dados_service import PreparationError, private_path
from app.services.previsao_service import PrevisaoError


SCHEMA_VERSION = "tccii-validation-v1"
CODE_ROOT = Path(__file__).resolve().parents[2]
REQUIRED_SCENARIOS = {f"atendimento.tcc_i_{index:02}" for index in range(1, 9)} | {
    "estoque." + name for name in ("entrada", "saida", "ajuste", "venda", "insuficiente", "zerado",
        "zerado_rejeita_venda", "minimo", "recuperacao", "deduplicacao", "materia_prima", "preventivo",
        "horizontes", "igualdade_preventiva")}
REQUIRED_CHECKS = REQUIRED_SCENARIOS | {"infraestrutura.preparacao_ficticia", "infraestrutura.inicializacao_xmpp",
    "comunicacao.tres_agentes_online", "comunicacao.atendimento_para_estoque", "comunicacao.estoque_para_atendimento",
    "comunicacao.estoque_para_previsao", "comunicacao.previsao_para_estoque", "infraestrutura.encerramento",
    "comunicacao.auditoria_correlacionada_redigida"}


def _environment():
    libraries = {}
    for name in ("Flask", "SQLAlchemy", "spade", "pyjabber", "numpy", "pandas", "scikit-learn", "threadpoolctl"):
        try:
            libraries[name] = version(name)
        except PackageNotFoundError:
            libraries[name] = None
    source_files = [*sorted((CODE_ROOT / "app").rglob("*.py")),
                    *sorted((CODE_ROOT / "scripts").rglob("*.py")),
                    CODE_ROOT / "requirements.txt", CODE_ROOT / "requirements-dev.txt"]
    hashes = {path.relative_to(CODE_ROOT).as_posix(): sha256(path.read_bytes()).hexdigest()
              for path in source_files if path.is_file()}
    def git(*args):
        return subprocess.run(["git", "-C", str(CODE_ROOT), *args], capture_output=True, check=False)
    head, status = git("rev-parse", "HEAD"), git("status", "--porcelain", "--untracked-files=normal")
    return {"python": platform.python_version(), "platform": platform.platform(), "libraries": libraries,
            "git_head": head.stdout.decode().strip() if head.returncode == 0 else None,
            "worktree_dirty": bool(status.stdout) if status.returncode == 0 else None,
            "experiment_source_sha256": hashes}


def _functional_result(result):
    """Evita que um retorno parcial ou vazio seja classificado como aprovado."""
    if (type(result) is not dict or result.get("status") not in {"aprovado", "falhou"}
            or result.get("data_origin") != "inteiramente_ficticia"
            or result.get("production_database_used") is not False
            or type(result.get("checks")) is not list or not result["checks"]):
        raise ExperimentError("EXPERIMENT_FAILED")
    checks = result["checks"]
    if any(type(item) is not dict or type(item.get("id")) is not str
           or item.get("status") not in {"aprovado", "falhou"} for item in checks):
        raise ExperimentError("EXPERIMENT_FAILED")
    identifiers = [item["id"] for item in checks]
    if len(set(identifiers)) != len(identifiers):
        raise ExperimentError("EXPERIMENT_FAILED")
    if result["status"] == "aprovado" and any(item["status"] != "aprovado" for item in checks):
        raise ExperimentError("EXPERIMENT_FAILED")
    if result["status"] == "aprovado" and not REQUIRED_CHECKS <= set(identifiers):
        raise ExperimentError("EXPERIMENT_FAILED")
    communication = result.get("communication")
    if result["status"] == "aprovado" and (type(communication) is not dict
            or communication.get("transport") != "xmpp_localhost"
            or type(communication.get("sent")) is not int or communication["sent"] <= 0
            or communication["sent"] != communication.get("received")
            or communication.get("failure_sent") != communication.get("failure_received")
            or communication.get("failure_sent") != 2):
        raise ExperimentError("EXPERIMENT_FAILED")
    for item in result.get("timings", []):
        seconds = item.get("seconds")
        if type(seconds) not in (int, float) or not math.isfinite(seconds) or seconds < 0:
            raise ExperimentError("EXPERIMENT_FAILED")
    return result


def _failed_functional():
    return {"status": "falhou", "data_origin": "inteiramente_ficticia",
        "production_database_used": False, "checks": [{"id": "infraestrutura.execucao_completa",
            "status": "falhou", "detail": "O experimento funcional não concluiu os critérios."}],
        "communication": None, "timings": []}


def run_validation(project_root, manifests=(), *, functional_only=False):
    """Retorna apenas caminhos/status; todos os conteúdos ficam em data/."""
    if (type(functional_only) is not bool or not isinstance(manifests, (list, tuple))
            or (functional_only and manifests) or (not functional_only and not manifests)
            or len(manifests) > 10 or any(not isinstance(item, (str, Path)) for item in manifests)):
        raise ExperimentError("INVALID_INPUT")
    root = Path(project_root).resolve()
    started = monotonic()
    run_id = "validacao-tccii-" + uuid4().hex
    created = datetime.now(timezone.utc).isoformat()
    try:
        workspace = private_path(root, "data/artifacts/" + run_id)
        report_directory = private_path(root, "data/reports/" + run_id)
        forecast_evidence = [collect_forecast_evidence(root, path) for path in manifests]
        origins = [evidence["run"]["run_id"] for evidence in forecast_evidence]
        if len(origins) != len(set(origins)):
            raise ExperimentError("INVALID_INPUT")
        for relative in ("data", "data/artifacts", "data/reports"):
            private_path(root, relative).mkdir(mode=0o700, exist_ok=True)
        workspace.mkdir(mode=0o700)
        report_directory.mkdir(mode=0o700)
    except ExperimentError:
        raise
    except PreparationError:
        raise ExperimentError("INVALID_PATH") from None
    except PrevisaoError:
        raise ExperimentError("INVALID_ARTIFACT") from None
    except Exception:
        raise ExperimentError("REPORT_FAILED") from None
    environment = _environment()
    try:
        functional = _functional_result(run_functional_experiment(workspace))
    except Exception:
        functional = _failed_functional()
    approved = functional["status"] == "aprovado"
    evaluated_forecasts = any(item["summary"]["evaluated_final_test"] > 0 for item in forecast_evidence)
    report = {"schema_version": SCHEMA_VERSION, "run_id": run_id, "created_at": created,
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "elapsed_seconds_including_setup": monotonic() - started,
        "status": "aprovado" if approved else "falhou",
        "scope": "somente_funcional" if functional_only else "funcional_e_previsao",
        "forecast_final_test_available": evaluated_forecasts,
        "phase10_evidence_complete": approved and not functional_only and evaluated_forecasts,
        "environment": environment,
        "workspace": workspace.relative_to(root).as_posix(),
        "functional": functional, "forecasts": forecast_evidence,
        "limitations": [
            "Os cenários de atendimento e estoque usam dados inteiramente fictícios e não medem ganhos operacionais da fábrica.",
            "Cada tempo descreve uma única execução de um caso completo, incluindo verificações; não representa latência de RPC isolada ou SLA.",
            "As métricas reais são evidências das origens temporais publicadas; não há teste de significância nem garantia de melhora futura.",
            "MAE/RMSE de produtos com unidades diferentes não são agregados em uma métrica geral.",
            "Não houve novo treinamento com histórico real, avanço da referência histórica, ativação de previsão ou alteração do banco operacional.",
        ]}
    paths = export_reports(root, report_directory, report)
    return {"status": report["status"], "scope": report["scope"], "run_id": run_id,
            "evidence_complete": report["phase10_evidence_complete"], **paths}
