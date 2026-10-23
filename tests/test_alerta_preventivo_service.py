"""Risco preventivo transacional com fontes e bancos exclusivamente fictícios."""

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import date
from decimal import Decimal, localcontext
from threading import Event
from uuid import uuid4

import pytest
from sqlalchemy import event, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.database import db
from app.models.alerta_estoque import AlertaEstoque
from app.models.materia_prima import MateriaPrima
from app.models.movimentacao_estoque import MovimentacaoEstoque
from app.models.previsao_demanda import PrevisaoDemanda
from app.models.previsao_execucao import PrevisaoExecucao
from app.models.produto import Produto
from app.models.venda import Venda
from app.services import alerta_preventivo_service as preventive
from app.services import previsao_repository as repository
from app.services.estoque_service import EstoqueError
from app.services.previsao_service import _periods
from test_forecasting_service import project
from test_previsao_service import configured


TODAY = date(2026, 1, 11)


@pytest.fixture
def ready(configured):
    service, paths, (product, _, _) = configured
    service.carregar(paths["manifest"])
    with service.app.app_context():
        item = db.session.get(Produto, product)
        item.quantidade_disponivel, item.estoque_minimo = 5, 3
        db.session.commit()
    return service, product, service.consultar(product)


def sync(ready, **kwargs):
    service, product, payload = ready
    with service.app.app_context():
        return preventive.sincronizar_alerta_preventivo(product, payload, hoje=TODAY, **kwargs)


def edit_product(ready, **kwargs):
    service, product, _ = ready
    with service.app.app_context():
        item = db.session.get(Produto, product)
        for key, value in kwargs.items():
            setattr(item, key, value)
        db.session.commit()


def assert_preserved(ready, expected):
    service, _, _ = ready
    with service.app.app_context():
        assert AlertaEstoque.query.one().previsao_json == expected
        assert AlertaEstoque.query.one().ativo


def test_ciclo_criar_atualizar_resolver_reabrir_preserva_historico(ready):
    first = sync(ready)
    assert first["risco"] and first["acao"] == "criado"
    assert first["saldo_projetado"] == "-5"
    assert first["disponivel"] == 5 and first["estoque_minimo"] == 3
    second = sync(ready)
    assert second["acao"] == "atualizado" and second["alerta_id"] == first["alerta_id"]
    edit_product(ready, quantidade_disponivel=20)
    resolved = sync(ready)
    assert not resolved["risco"] and resolved["acao"] == "resolvido"
    assert resolved["alerta_id"] == first["alerta_id"]
    assert sync(ready)["acao"] == "sem_alerta"
    edit_product(ready, quantidade_disponivel=5)
    reopened = sync(ready)
    assert reopened["acao"] == "criado" and reopened["alerta_id"] != first["alerta_id"]
    with ready[0].app.app_context():
        old, new = AlertaEstoque.query.order_by(AlertaEstoque.id).all()
        assert not old.ativo and old.resolvido_em and old.atualizado_em
        assert new.ativo and new.resolvido_em is None
        assert set(new.previsao_json) == {"previsao", "disponivel", "estoque_minimo", "saldo_projetado", "avaliada_em"}
        assert new.previsao_json["previsao"] == ready[2]
        assert "mínimo 3" in new.mensagem and "7 dias" in new.mensagem


def test_igualdade_ao_minimo_nao_e_risco_preventivo(ready):
    edit_product(ready, quantidade_disponivel=13)
    result = sync(ready)
    assert result["saldo_projetado"] == "3"
    assert not result["risco"] and result["acao"] == "sem_alerta" and result["alerta_id"] is None
    with ready[0].app.app_context():
        assert AlertaEstoque.query.count() == 0


def test_horizonte_solicitado_e_independente_da_previsao_padrao(ready):
    service, product, _ = ready
    payload = service.consultar(product, horizonte_dias=3)
    with service.app.app_context():
        result = preventive.sincronizar_alerta_preventivo(product, payload, horizonte_dias=3, hoje=TODAY)
    assert result["previsao"]["horizonte_dias"] == 3 and result["saldo_projetado"] == "1"
    assert result["risco"]


def test_zero_previsto_valido_pode_resolver_alerta_sem_inventar_demanda(ready):
    sync(ready)
    service, product, _ = ready
    payload = service.consultar(product, horizonte_dias=1)
    assert payload["total_previsto"] == "0"  # Domingo no calendário fictício.
    with service.app.app_context():
        result = preventive.sincronizar_alerta_preventivo(product, payload, horizonte_dias=1, hoje=TODAY)
        assert result["acao"] == "resolvido" and result["saldo_projetado"] == "5"
        alert = AlertaEstoque.query.one()
        state = preventive.estado_alerta_preventivo(alert, db.session.get(Produto, product), payload["execucao_id"], hoje=TODAY)
        assert state == {"atual": False, "motivo": "resolvido"}


def test_payload_do_chamador_nao_compartilha_snapshot_persistido(ready):
    sync(ready)
    ready[2]["periodos"][0]["quantidade_prevista"] = "999"
    with ready[0].app.app_context():
        assert AlertaEstoque.query.one().previsao_json["previsao"]["periodos"][0]["quantidade_prevista"] == "0"


@pytest.mark.parametrize("changed", [None, {}, {"periodos": []}, {"produto_id": None},
    {"granularidade": "semanal"}, {"produto_id": 999}, {"horizonte_dias": 6},
    {"total_previsto": "0"}, {"total_previsto": "NaN"}, {"modelo": {}},
    {"gerada_em": "SEM_FUSO"}, {"campo_inventado": "DADO_FICTICIO_RESTRITO"}])
def test_resposta_invalida_nunca_resolve_alerta_ativo(ready, changed):
    sync(ready)
    with ready[0].app.app_context():
        original = deepcopy(AlertaEstoque.query.one().previsao_json)
    payload = deepcopy(ready[2])
    payload = changed if changed in (None, {}) else {**payload, **changed}
    with ready[0].app.app_context(), pytest.raises(EstoqueError) as error:
        preventive.sincronizar_alerta_preventivo(ready[1], payload, hoje=TODAY)
    assert error.value.code == "INVALID_RESPONSE"
    assert "DADO_FICTICIO_RESTRITO" not in str(error.value)
    assert_preserved(ready, original)


@pytest.mark.parametrize("today", [date(2026, 1, 10), date(2026, 1, 12)])
def test_previsao_antiga_ou_futura_nao_resolve_alerta(ready, today):
    sync(ready)
    with ready[0].app.app_context(), pytest.raises(EstoqueError) as error:
        preventive.sincronizar_alerta_preventivo(ready[1], ready[2], hoje=today)
    assert error.value.code == "FORECAST_STALE"
    with ready[0].app.app_context():
        assert AlertaEstoque.query.one().ativo


@pytest.mark.parametrize("field,value", [("execucao_id", "previsao-inventada"),
    ("modelo", "random_forest"), ("versao", "forecast-outro"),
    ("gerada_em", "2026-01-11T12:00:00+00:00"), ("codigo_produto", "110")])
def test_metadados_divergentes_do_snapshot_ativo_bloqueiam(ready, field, value):
    sync(ready)
    payload = {**ready[2], field: value}
    with ready[0].app.app_context(), pytest.raises(EstoqueError) as error:
        preventive.sincronizar_alerta_preventivo(ready[1], payload, hoje=TODAY)
    assert error.value.code == "FORECAST_CHANGED"
    with ready[0].app.app_context():
        assert AlertaEstoque.query.one().ativo


def test_mesmo_total_com_diarios_diferentes_nao_e_aceito(ready):
    payload = deepcopy(ready[2])
    payload["periodos"][1]["quantidade_prevista"] = "1"
    payload["periodos"][2]["quantidade_prevista"] = "3"
    with ready[0].app.app_context(), pytest.raises(EstoqueError) as error:
        preventive.sincronizar_alerta_preventivo(ready[1], payload, hoje=TODAY)
    assert error.value.code == "FORECAST_CHANGED"


@pytest.mark.parametrize("change,code", [({"ativo": False}, "PRODUCT_NOT_FOUND"),
    ({"codigo_externo": None}, "PRODUCT_UNMAPPED"), ({"codigo_externo": "000110"}, "FORECAST_CHANGED")])
def test_rele_catalogo_depois_da_consulta_sem_apagar_alerta(ready, change, code):
    sync(ready)
    edit_product(ready, **change)
    with ready[0].app.app_context(), pytest.raises(EstoqueError) as error:
        preventive.sincronizar_alerta_preventivo(ready[1], ready[2], hoje=TODAY)
    assert error.value.code == code
    with ready[0].app.app_context():
        assert AlertaEstoque.query.one().ativo


def test_rele_saldo_e_minimo_apos_resposta_remota(ready):
    edit_product(ready, quantidade_disponivel=30, estoque_minimo=25)
    result = sync(ready)
    assert result["risco"] and result["saldo_projetado"] == "20"
    assert result["disponivel"] == 30 and result["estoque_minimo"] == 25


def test_execucao_trocada_durante_rpc_nao_resolve_alerta(ready):
    service, product, _ = ready
    sync(ready)
    with service.app.app_context():
        active = db.session.scalars(select(PrevisaoExecucao).where(PrevisaoExecucao.ativa.is_(True))).one()
        result = deepcopy(active.resultado_json)
        report = {**active.metadados_json, "evaluations": deepcopy(active.metricas_json)}
        run_id = "previsao-" + uuid4().hex
        result["run_id"] = report["run_id"] = run_id
        db.session.remove()
        repository.persistir_execucao(manifest_path=f"data/artifacts/{run_id}/manifest.json",
            manifest_sha256="a" * 64, result=result, report=report)
        with pytest.raises(EstoqueError) as error:
            preventive.sincronizar_alerta_preventivo(product, ready[2], hoje=TODAY)
        assert error.value.code == "FORECAST_CHANGED" and AlertaEstoque.query.one().ativo


def test_execucao_desativada_nao_resolve_alerta(ready):
    sync(ready)
    with ready[0].app.app_context():
        PrevisaoExecucao.query.one().ativa = False
        db.session.commit()
        with pytest.raises(EstoqueError) as error:
            preventive.sincronizar_alerta_preventivo(ready[1], ready[2], hoje=TODAY)
        assert error.value.code == "FORECAST_CHANGED" and AlertaEstoque.query.one().ativo


def test_decimal_exato_sem_arredondar_saldo_projetado(ready):
    service, product, payload = ready
    with service.app.app_context():
        active = db.session.get(PrevisaoExecucao, payload["execucao_id"])
        result = deepcopy(active.resultado_json)
        days = [date.fromisoformat(row["periodo_inicio"]) for row in result["forecast"]["products"]["00110"]["diaria"]]
        with localcontext() as context:
            context.prec = 450
            product_values = [Decimal("0.333333333")] * len(days)
            zeroes = [Decimal(0)] * len(days)
            for code, values in (("00110", product_values), ("00220", zeroes)):
                result["forecast"]["products"][code] = {frequency: _periods(days, values, frequency)
                    for frequency in ("diaria", "semanal", "mensal")}
                result["forecast"]["products"][code]["total_previsto"] = "3.33333333" if code == "00110" else "0"
            result["forecast"]["overall"] = deepcopy(result["forecast"]["products"]["00110"])
        active.resultado_json = result
        db.session.commit()
        payload = deepcopy(payload)
        payload["periodos"] = result["forecast"]["products"]["00110"]["diaria"][:7]
        payload["total_previsto"] = "2.333333331"
        output = preventive.sincronizar_alerta_preventivo(product, payload, hoje=TODAY)
    assert output["saldo_projetado"] == "2.666666669" and output["risco"]


def test_avaliacoes_concorrentes_nao_duplicam_alerta(ready):
    service, product, payload = ready
    def run(_):
        with service.app.app_context():
            return preventive.sincronizar_alerta_preventivo(product, payload, hoje=TODAY)
    with ThreadPoolExecutor(max_workers=4) as executor:
        results = list(executor.map(run, range(4)))
    assert len({result["alerta_id"] for result in results}) == 1
    assert [result["acao"] for result in results].count("criado") == 1
    with service.app.app_context():
        assert AlertaEstoque.query.count() == 1


def test_falha_ao_gravar_reverte_snapshot_e_estado(ready):
    sync(ready)
    with ready[0].app.app_context():
        previous = deepcopy(AlertaEstoque.query.one().previsao_json)
    edit_product(ready, quantidade_disponivel=20)
    def fail(session, *_):
        if any(isinstance(item, AlertaEstoque) for item in session.dirty):
            raise RuntimeError("Falha fictícia de persistência")
    event.listen(Session, "before_flush", fail)
    try:
        with pytest.raises(RuntimeError):
            sync(ready)
    finally:
        event.remove(Session, "before_flush", fail)
    assert_preserved(ready, previous)


def test_erro_do_driver_e_sanitizado_e_transacao_revertida(ready):
    def fail(session, *_):
        if any(isinstance(item, AlertaEstoque) for item in session.new):
            raise SQLAlchemyError("CONTEUDO_FICTICIO_DO_DRIVER")
    event.listen(Session, "before_flush", fail)
    try:
        with pytest.raises(EstoqueError) as error:
            sync(ready)
    finally:
        event.remove(Session, "before_flush", fail)
    assert error.value.code == "STORAGE_ERROR"
    assert "CONTEUDO_FICTICIO" not in str(error.value)
    with ready[0].app.app_context():
        assert AlertaEstoque.query.count() == 0


def test_cancelamento_previo_nao_inicia_avaliacao(ready):
    cancelled = Event()
    cancelled.set()
    with pytest.raises(EstoqueError) as error:
        sync(ready, cancel_event=cancelled)
    assert error.value.code == "FORECAST_CANCELLED"
    with ready[0].app.app_context():
        assert AlertaEstoque.query.count() == 0


@pytest.mark.parametrize("existing", [False, True])
def test_cancelamento_durante_gravacao_reverte_criacao_ou_resolucao(ready, existing):
    previous = None
    if existing:
        sync(ready)
        with ready[0].app.app_context():
            previous = deepcopy(AlertaEstoque.query.one().previsao_json)
        edit_product(ready, quantidade_disponivel=20)
    cancelled = Event()
    def cancel_after_flush(session, *_):
        if any(isinstance(item, AlertaEstoque) for item in (*session.new, *session.dirty)):
            cancelled.set()
    event.listen(Session, "after_flush", cancel_after_flush)
    try:
        with pytest.raises(EstoqueError) as error:
            sync(ready, cancel_event=cancelled)
    finally:
        event.remove(Session, "after_flush", cancel_after_flush)
    assert cancelled.is_set() and error.value.code == "FORECAST_CANCELLED"
    with ready[0].app.app_context():
        if existing:
            alert = AlertaEstoque.query.one()
            assert alert.ativo and alert.resolvido_em is None and alert.previsao_json == previous
        else:
            assert AlertaEstoque.query.count() == 0


def test_nao_modifica_estoque_previsoes_imediatos_ou_materia_prima(ready):
    service, product, _ = ready
    with service.app.app_context():
        immediate = AlertaEstoque(tipo_item="produto", produto_id=product, tipo_alerta="estoque_minimo",
                                  nivel="aviso", mensagem="Alerta fictício imediato", ativo=True)
        material = MateriaPrima(codigo="MP-FICTICIA", nome="Matéria-prima fictícia", unidade_medida="kg",
                                 quantidade_disponivel=Decimal("3.5"), estoque_minimo=Decimal("1"))
        db.session.add_all([immediate, material])
        db.session.commit()
        rows = [(row.id, row.quantidade_exata) for row in PrevisaoDemanda.query.all()]
        execution = deepcopy(PrevisaoExecucao.query.one().resultado_json)
    sync(ready)
    with service.app.app_context():
        item = db.session.get(Produto, product)
        assert (item.quantidade_disponivel, item.estoque_minimo, item.codigo_externo) == (5, 3, "00110")
        assert [(row.id, row.quantidade_exata) for row in PrevisaoDemanda.query.all()] == rows
        assert PrevisaoExecucao.query.one().resultado_json == execution
        assert AlertaEstoque.query.filter_by(tipo_alerta="estoque_minimo").one().mensagem == "Alerta fictício imediato"
        assert MateriaPrima.query.one().quantidade_disponivel == Decimal("3.5")
        assert MovimentacaoEstoque.query.count() == Venda.query.count() == 0


@pytest.mark.parametrize("change,active,today,reason", [({}, None, TODAY, "execucao_alterada"),
    ({"quantidade_disponivel": 6}, "same", TODAY, "estoque_alterado"),
    ({"estoque_minimo": 4}, "same", TODAY, "minimo_alterado"),
    ({"codigo_externo": "110"}, "same", TODAY, "associacao_alterada"),
    ({"ativo": False}, "same", TODAY, "produto_inativo"),
    ({}, "same", date(2026, 1, 12), "previsao_vencida"),
    ({}, "same", TODAY, "atual")])
def test_helper_informa_desatualizacao_sem_mudar_registros(ready, change, active, today, reason):
    sync(ready)
    service, product, payload = ready
    edit_product(ready, **change)
    with service.app.app_context():
        alert = AlertaEstoque.query.one()
        before = (deepcopy(alert.previsao_json), alert.atualizado_em, alert.ativo)
        item = db.session.get(Produto, product)
        execution = payload["execucao_id"] if active == "same" else active
        state = preventive.estado_alerta_preventivo(alert, item, execution, hoje=today)
        assert state == {"atual": reason == "atual", "motivo": reason}
        assert (alert.previsao_json, alert.atualizado_em, alert.ativo) == before
        assert not db.session.dirty


@pytest.mark.parametrize("snapshot,reason", [(None, "legado_sem_previsao"), ({}, "snapshot_invalido"),
    ({"previsao": None}, "snapshot_invalido")])
def test_helper_reconhece_legado_e_snapshot_invalido(ready, snapshot, reason):
    sync(ready)
    with ready[0].app.app_context():
        alert = AlertaEstoque.query.one()
        alert.previsao_json = snapshot
        state = preventive.estado_alerta_preventivo(alert, db.session.get(Produto, ready[1]), ready[2]["execucao_id"], hoje=TODAY)
        assert state == {"atual": False, "motivo": reason}


@pytest.mark.parametrize("horizon", [0, 367, True, "7"])
def test_horizonte_invalido_nao_cria_alerta(ready, horizon):
    with ready[0].app.app_context(), pytest.raises(EstoqueError) as error:
        preventive.sincronizar_alerta_preventivo(ready[1], ready[2], horizon, hoje=TODAY)
    assert error.value.code == "INVALID_INPUT"
