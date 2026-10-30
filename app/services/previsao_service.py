"""Adapta artefatos privados do motor ao agente e à persistência transacional."""

from calendar import monthrange
from datetime import date, datetime, timedelta
from decimal import Decimal, localcontext
from hashlib import sha256
import json
import os
from pathlib import Path
import re
from threading import Event
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from app.database import db
from app.forecasting.errors import ForecastError
from app.forecasting.service import ENGINE_VERSION, run_forecast
from app.models.produto import Produto
from app.services.preparacao_dados_service import private_path
from app.services import previsao_repository as repository


PROJECT_ROOT = Path(__file__).resolve().parents[2]


class PrevisaoError(ValueError):
    def __init__(self, code, message):
        self.code = code
        super().__init__(message)


def hoje_manaus():
    return datetime.now(ZoneInfo("America/Manaus")).date()


def _day(value):
    try:
        if not isinstance(value, str) or date.fromisoformat(value).isoformat() != value:
            raise ValueError
        return date.fromisoformat(value)
    except (TypeError, ValueError):
        raise PrevisaoError("INVALID_INPUT", "Informe uma data comercial válida no formato YYYY-MM-DD.") from None


def _horizon(value):
    if type(value) is not int or not 1 <= value <= 366:
        raise PrevisaoError("INVALID_INPUT", "Informe um horizonte entre 1 e 366 dias.")
    return value


def _text(value):
    text = format(value, "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


def _periods(days, values, frequency):
    result = []
    for day, value in zip(days, values):
        if frequency == "diaria":
            first, last = day, day
        elif frequency == "semanal":
            first = day - timedelta(days=day.weekday())
            last = first + timedelta(days=6)
        else:
            first = day.replace(day=1)
            last = day.replace(day=monthrange(day.year, day.month)[1])
        if not result or result[-1]["periodo_inicio"] != first.isoformat():
            result.append({"periodo_inicio": first.isoformat(), "periodo_fim": last.isoformat(),
                           "cobertura_inicio": day.isoformat(), "cobertura_fim": day.isoformat(),
                           "parcial": True, "quantidade_prevista": Decimal(0)})
        period = result[-1]
        period["cobertura_fim"] = day.isoformat()
        period["quantidade_prevista"] += value
        period["parcial"] = period["cobertura_inicio"] != period["periodo_inicio"] or period["cobertura_fim"] != period["periodo_fim"]
    for period in result:
        period["quantidade_prevista"] = _text(period["quantidade_prevista"])
    return result


class PrevisaoService:
    def __init__(self, app):
        self.app = app
        self.root = Path(app.config.get("FORECAST_PROJECT_ROOT", PROJECT_ROOT)).resolve()

    def _config(self, key, default=None):
        if self.app.testing:
            # Testes nunca herdam caminhos privados definidos no shell do usuário.
            return self.app.config.get(key, default)
        return self.app.config.get(key, os.environ.get(key, default))

    def _file(self, candidate, directory):
        path = private_path(self.root, candidate)
        if not path.is_relative_to(self.root / "data" / directory) or not path.is_file() or path.stat().st_nlink != 1:
            raise PrevisaoError("INVALID_ARTIFACT", "Artefato privado ausente ou fora do diretório permitido.")
        return path

    @staticmethod
    def _json(content):
        def invalid(_):
            raise ValueError
        return json.loads(content, parse_constant=invalid)

    def _bundle(self, manifest_path, expected_hash=None, *, check_all=True):
        """Valida a execução; consultas leem só os JSON que consomem."""
        try:
            path = self._file(manifest_path, "artifacts")
            content = path.read_bytes()
            digest = sha256(content).hexdigest()
            if expected_hash is not None and digest != expected_hash:
                raise ValueError
            manifest = self._json(content)
            if (type(manifest) is not dict or manifest.get("status") != "concluido"
                    or manifest.get("version") != ENGINE_VERSION
                    or not re.fullmatch(r"previsao-[a-f0-9]{32}", manifest.get("run_id", ""))
                    or path.parent.name != manifest["run_id"]
                    or type(manifest.get("files_sha256")) is not dict
                    or type(manifest.get("models")) is not dict or not manifest["models"]):
                raise ValueError
            hashes = manifest["files_sha256"]
            required = {manifest["forecast_file"], manifest["report_file"], manifest["summary_file"], *manifest["models"].values()}
            if not required <= set(hashes):
                raise ValueError
            contents = {}
            for relative, expected in hashes.items():
                if not isinstance(relative, str) or not re.fullmatch(r"[a-f0-9]{64}", expected):
                    raise ValueError
                if not check_all and relative not in {manifest["forecast_file"], manifest["report_file"]}:
                    continue
                directory = "reports" if relative in {manifest["report_file"], manifest["summary_file"]} else (
                    "processed" if relative.startswith("data/processed/") else "artifacts")
                target = self._file(relative, directory)
                if directory != "reports" and not target.is_relative_to(self.root / "data" / directory / manifest["run_id"]):
                    raise ValueError
                if directory == "reports" and target.stem != manifest["run_id"]:
                    raise ValueError
                data = target.read_bytes()
                if sha256(data).hexdigest() != expected:
                    raise ValueError
                if relative in {manifest["forecast_file"], manifest["report_file"]}:
                    contents[relative] = data
            result = self._json(contents[manifest["forecast_file"]])
            report = self._json(contents[manifest["report_file"]])
            if (result["run_id"] != manifest["run_id"] or result["generated_at"] != manifest["generated_at"]
                    or set(result["models"]) != set(manifest["models"])):
                raise ValueError
            repository.validar_execucao(result, report)
            return path, digest, result, report
        except PrevisaoError:
            raise
        except Exception:
            raise PrevisaoError("INVALID_ARTIFACT", "A execução de previsão está ausente, incompleta, incompatível ou foi alterada.") from None

    def carregar(self, manifest_path, *, cancel_event=None):
        path, digest, result, report = self._bundle(manifest_path)
        if cancel_event is not None and cancel_event.is_set():
            raise PrevisaoError("UPDATE_CANCELLED", "A atualização foi interrompida antes da ativação.")
        try:
            with self.app.app_context():
                return repository.persistir_execucao(manifest_path=path.relative_to(self.root).as_posix(),
                    manifest_sha256=digest, result=result, report=report, cancel_event=cancel_event)
        except repository.RepositoryError as error:
            raise PrevisaoError(error.code, str(error)) from None

    def inicializar(self, *, cancel_event=None):
        configured = self._config("FORECAST_MANIFEST")
        if configured:
            self.carregar(configured, cancel_event=cancel_event)
            return
        try:
            with self.app.app_context():
                active = repository.carregar_ativa()
            if active is not None:
                self._bundle(active["manifest_path"], active["manifest_sha256"])
        except repository.RepositoryError as error:
            raise PrevisaoError(error.code, str(error)) from None

    def consultar(self, produto_id=None, granularidade="diaria", horizonte_dias=7):
        _horizon(horizonte_dias)
        if granularidade not in {"diaria", "semanal", "mensal"} or (
            produto_id is not None and (type(produto_id) is not int or produto_id <= 0)
        ):
            raise PrevisaoError("INVALID_INPUT", "Parâmetros de consulta de previsão inválidos.")
        try:
            with self.app.app_context():
                active = repository.carregar_ativa()
                if active is None:
                    raise PrevisaoError("MODEL_UNAVAILABLE", "Nenhuma execução de previsão foi ativada.")
                code = None
                if produto_id is not None:
                    product = db.session.get(Produto, produto_id)
                    if product is None:
                        raise PrevisaoError("PRODUCT_NOT_FOUND", "Produto não encontrado no catálogo.")
                    if not product.codigo_externo:
                        raise PrevisaoError("PRODUCT_UNMAPPED", "O produto não possui código externo associado ao histórico.")
                    code = product.codigo_externo
            _, _, result, _ = self._bundle(active["manifest_path"], active["manifest_sha256"], check_all=False)
            if result != active["result"]:
                raise PrevisaoError("INVALID_ARTIFACT", "A previsão diverge da execução persistida.")
            forecast = result["forecast"]["overall"] if code is None else result["forecast"]["products"].get(code)
            if forecast is None:
                raise PrevisaoError("HISTORY_NOT_FOUND", "O produto não possui histórico elegível nesta execução.")
            if produto_id is not None:
                from app.services.catalogacao_service import associacao_revisada
                with self.app.app_context(), Session(db.engine) as session:
                    current_product = session.get(Produto, produto_id)
                    if (current_product is None or current_product.codigo_externo != code
                            or not associacao_revisada(session, current_product)):
                        raise PrevisaoError("MAPPING_REVIEW_REQUIRED",
                            "O vínculo histórico está pendente de revisão explícita em Catalogação.")
            if horizonte_dias > result["horizon_days"]:
                raise PrevisaoError("HORIZON_UNAVAILABLE", "O horizonte solicitado excede a cobertura da previsão ativa.")
            daily = forecast["diaria"][:horizonte_dias]
            days = [_day(item["periodo_inicio"]) for item in daily]
            values = [Decimal(item["quantidade_prevista"]) for item in daily]
            with localcontext() as context:
                context.prec = 400
                return {"produto_id": produto_id, "codigo_produto": code, "granularidade": granularidade,
                    "horizonte_dias": horizonte_dias, "data_corte": result["cutoff"],
                    "cobertura_inicio": days[0].isoformat(), "cobertura_fim": days[-1].isoformat(),
                    "total_previsto": _text(sum(values, Decimal(0))), "periodos": _periods(days, values, granularidade),
                    "modelo": result["models"] if code is None else result["models"][code],
                    "gerada_em": result["generated_at"], "versao": result["version"], "execucao_id": result["run_id"]}
        except PrevisaoError:
            raise
        except repository.RepositoryError as error:
            raise PrevisaoError(error.code, str(error)) from None
        except Exception:
            raise PrevisaoError("STORAGE_ERROR", "Não foi possível consultar a execução de previsão.") from None

    def painel(self, codigo_produto=None, granularidade="diaria", data_inicio=None, data_fim=None):
        from app.services.previsao_painel_service import painel
        return painel(self, codigo_produto, granularidade, data_inicio, data_fim)

    def recalcular(self, data_corte, horizonte_dias, *, cancel_event=None):
        cutoff, horizon = _day(data_corte), _horizon(horizonte_dias)
        if cutoff >= hoje_manaus():
            raise PrevisaoError("INVALID_INPUT", "A atualização exige uma data de corte de dia completo anterior a hoje.")
        review = self._config("FORECAST_REVIEW")
        if not review:
            raise PrevisaoError("REVIEW_UNAVAILABLE", "Configure uma revisão histórica aprovada antes de atualizar a previsão.")
        cancelled = cancel_event or Event()

        def progress():
            if cancelled.is_set():
                raise PrevisaoError("UPDATE_CANCELLED", "A atualização foi interrompida antes da ativação.")

        try:
            progress()
            paths = run_forecast(self.root, review, cutoff=data_corte, horizon=horizon,
                start=self._config("FORECAST_START", "2022-01-01"),
                calendar_overrides=self._config("FORECAST_CALENDAR_OVERRIDES") or None, progress=progress)
            progress()
            return self.carregar(paths["manifest"], cancel_event=cancelled)
        except PrevisaoError:
            raise
        except ForecastError:
            if cancelled.is_set():
                raise PrevisaoError("UPDATE_CANCELLED", "A atualização foi interrompida antes da ativação.") from None
            raise PrevisaoError("UPDATE_FAILED", "O motor não concluiu a atualização; confira a revisão, o calendário e os artefatos locais.") from None
        except Exception:
            raise PrevisaoError("UPDATE_FAILED", "Não foi possível concluir a atualização local da previsão.") from None

    def associar_produto(self, produto_id, codigo_externo, *, confirmar=False, operador=None, motivo=None):
        """Compatibilidade da CLI: usa a mesma confirmação e auditoria do catálogo."""
        if (type(produto_id) is not int or produto_id <= 0 or type(codigo_externo) is not str
                or not 1 <= len(codigo_externo) <= 64 or codigo_externo != codigo_externo.strip()
                or any(ord(char) < 32 or ord(char) == 127 for char in codigo_externo)):
            raise PrevisaoError("INVALID_INPUT", "Informe o ID do produto e seu código externo textual.")
        try:
            with self.app.app_context():
                active = repository.carregar_ativa()
                if active is None:
                    raise PrevisaoError("MODEL_UNAVAILABLE", "Ative uma execução revisada antes de associar produtos.")
                if codigo_externo not in active["result"]["forecast"]["products"]:
                    raise PrevisaoError("HISTORY_NOT_FOUND", "O código informado não possui histórico elegível na execução ativa.")
                from app.services.catalogacao_service import listar_catalogacao, executar_catalogacao
                from app.services.estoque_service import EstoqueError
                listing = listar_catalogacao()
                product = next((item for item in listing["produtos"] if item["id"] == produto_id), None)
                entry = next(item for item in listing["codigos"] if item["codigo_produto"] == codigo_externo)
                if product is None:
                    raise PrevisaoError("PRODUCT_NOT_FOUND", "Produto não encontrado no catálogo.")
                if (entry["produto_id"] not in (None, produto_id)
                        or product["codigo_externo"] not in (None, codigo_externo)):
                    raise PrevisaoError("MAPPING_CONFLICT", "Já existe uma associação diferente; revise o vínculo antes de alterá-lo.")
                try:
                    return executar_catalogacao(acao="revisar" if entry["produto_id"] == produto_id else "associar",
                        codigo_produto=codigo_externo, produto_id=produto_id, versao=entry["versao"],
                        versao_produto=product["versao"], confirmar=confirmar, operador=operador, motivo=motivo)
                except EstoqueError as error:
                    raise PrevisaoError(error.code, str(error)) from None
        except PrevisaoError:
            raise
        except Exception:
            raise PrevisaoError("STORAGE_ERROR", "Não foi possível registrar a associação explícita do produto.") from None
