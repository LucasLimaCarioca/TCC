"""Consulta de previsão e recálculo assíncrono pelo transporte XMPP local."""

import asyncio
from threading import Event
from uuid import uuid4

from app.agents.base_agent import BaseAgent
from app.agents.protocol import (PREVISAO, PREVISAO_ATUALIZAR, PREVISAO_PUBLICADA, PREVISAO_STATUS,
                                 encode, failure)
from app.services.previsao_service import PrevisaoError, PrevisaoService


class PrevisaoAgent(BaseAgent):
    INITIALIZATION_WAIT = 0.25
    MAX_JOBS = 128

    def __init__(self, password, app, port=5222, audit=None, *, service=None):
        super().__init__("previsao", password, port, audit)
        self.service = service if service is not None else PrevisaoService(app)
        self._initialization = None
        self._initialization_error = None
        self._job_task = None
        self._job_cancel = None
        self._active_job = None
        self._jobs = {}
        self._notification_tasks = set()
        self._stopped = False
        self._lifecycle_cancel = Event()

    async def setup(self):
        await super().setup()
        # A ausência de modelo não impede o funcionamento dos outros agentes.
        self._initialization = asyncio.create_task(self._initialize())

    async def _initialize(self):
        try:
            await asyncio.to_thread(self.service.inicializar, cancel_event=self._lifecycle_cancel)
        except PrevisaoError as error:
            self._initialization_error = (error.code, str(error))
        except Exception:
            self._initialization_error = ("INTERNAL_ERROR", "Não foi possível inicializar a previsão.")

    async def _wait_initialized(self):
        if self._initialization is None:
            raise PrevisaoError("FORECAST_INITIALIZING", "A previsão ainda está sendo inicializada.")
        try:
            await asyncio.wait_for(asyncio.shield(self._initialization), self.INITIALIZATION_WAIT)
        except asyncio.TimeoutError:
            raise PrevisaoError("FORECAST_INITIALIZING", "A previsão ainda está sendo inicializada.") from None

    async def aguardar_inicializacao(self):
        """CLI pode aguardar a carga fora da inbox, com timeout do runtime."""
        if self._initialization is None:
            raise PrevisaoError("FORECAST_INITIALIZING", "A previsão ainda está sendo inicializada.")
        await asyncio.shield(self._initialization)

    async def handle_request(self, sender, envelope):
        if envelope.ontology not in {PREVISAO, PREVISAO_ATUALIZAR, PREVISAO_STATUS}:
            return await super().handle_request(sender, envelope)
        try:
            if self._stopped:
                raise PrevisaoError("FORECAST_UNAVAILABLE", "O agente de previsão está encerrando.")
            if envelope.ontology == PREVISAO_STATUS:
                result = self._status(envelope.payload["job_id"])
            else:
                await self._wait_initialized()
                if envelope.ontology == PREVISAO:
                    if self._initialization_error is not None:
                        raise PrevisaoError(*self._initialization_error)
                    result = await asyncio.to_thread(self.service.consultar, **envelope.payload)
                else:
                    result = self._schedule(envelope.payload)
            return encode(sender, envelope.ontology, result, "inform", envelope.thread)
        except PrevisaoError as error:
            return failure(sender, envelope.ontology, envelope.thread, error.code, str(error))
        except Exception:
            return failure(sender, envelope.ontology, envelope.thread, "INTERNAL_ERROR",
                           "Não foi possível concluir a operação de previsão.")

    def _schedule(self, payload):
        # Executado somente no loop SPADE: verificação e reserva são atômicas.
        if self._stopped:
            raise PrevisaoError("FORECAST_UNAVAILABLE", "O agente de previsão está encerrando.")
        if self._active_job is not None:
            raise PrevisaoError("FORECAST_BUSY", "Já existe um recálculo de previsão em execução.")
        while len(self._jobs) >= self.MAX_JOBS:
            self._jobs.pop(next(iter(self._jobs)))
        job_id = str(uuid4())
        self._active_job = job_id
        self._job_cancel = Event()
        self._jobs[job_id] = {"job_id": job_id, "status": "em_execucao",
                              "execucao_id": None, "erro": None}
        self._job_task = asyncio.create_task(self._run_job(job_id, payload, self._job_cancel))
        return {"job_id": job_id, "status": "em_execucao"}

    async def _run_job(self, job_id, payload, cancel_event):
        try:
            if cancel_event.is_set() or self._stopped:
                raise PrevisaoError("FORECAST_CANCELLED", "O recálculo foi cancelado durante o encerramento.")
            result = await asyncio.to_thread(self.service.recalcular, **payload, cancel_event=cancel_event)
            if cancel_event.is_set() or self._stopped:
                raise PrevisaoError("FORECAST_CANCELLED", "O recálculo foi cancelado durante o encerramento.")
            if (type(result) is not dict or set(result) != {"run_id", "status"}
                    or result["status"] != "concluido" or type(result["run_id"]) is not str
                    or not 0 < len(result["run_id"]) <= 128):
                raise ValueError("Resultado interno inválido.")
            self._initialization_error = None
            self._jobs[job_id].update(status="concluido", execucao_id=result["run_id"])
            # A publicação já está concluída. O aviso roda fora deste job e da
            # inbox, sem transformar indisponibilidade do estoque em falha do modelo.
            notification = asyncio.create_task(self._notificar_publicacao(result["run_id"]))
            self._notification_tasks.add(notification)
            notification.add_done_callback(self._notification_tasks.discard)
        except PrevisaoError as error:
            self._jobs[job_id].update(status="falhou", erro={"code": error.code, "message": str(error)})
        except asyncio.CancelledError:
            cancel_event.set()
            self._jobs[job_id].update(status="falhou", erro={
                "code": "FORECAST_CANCELLED", "message": "O recálculo foi cancelado durante o encerramento."})
            raise
        except Exception:
            self._jobs[job_id].update(status="falhou", erro={
                "code": "INTERNAL_ERROR", "message": "Não foi possível concluir o recálculo da previsão."})
        finally:
            self._active_job = None

    async def _notificar_publicacao(self, run_id):
        try:
            await self.request("estoque", PREVISAO_PUBLICADA, {"execucao_id": run_id}, timeout=5)
        except Exception:
            # Inicialização, avaliação manual e mudanças de estoque recuperam
            # avisos perdidos; não registrar payloads ou exceções aqui.
            pass

    def _status(self, job_id):
        if job_id not in self._jobs:
            raise PrevisaoError("JOB_NOT_FOUND", "Recálculo não encontrado nesta execução do agente.")
        return dict(self._jobs[job_id])

    async def stop(self):
        self._stopped = True
        self._lifecycle_cancel.set()
        if self._job_cancel is not None:
            self._job_cancel.set()
        notifications = tuple(self._notification_tasks)
        for task in notifications:
            task.cancel()
        await asyncio.gather(*notifications, return_exceptions=True)
        await super().stop()
