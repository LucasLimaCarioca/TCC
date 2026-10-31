"""Experimento funcional isolado, com dados fictícios e SPADE/XMPP reais.

O diretório recebido deve ser a pasta privada de uma nova execução. Nenhuma
configuração ou fonte histórica da aplicação do operador é herdada. As únicas
latências reportadas são medições monotônicas dos casos, sem startup/fixture.
"""

from collections import Counter
import csv
from datetime import datetime, timedelta
from decimal import Decimal
from hashlib import sha256
import io
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import time
from uuid import uuid4
from zoneinfo import ZoneInfo

from app.agents.base_agent import RemoteFailure
from app.agents.config import RuntimeConfig
from app.agents.gateway import AgentGateway
from app.agents.protocol import JIDS
from app.agents.runtime import AgentRuntime
from app.app import create_app
from app.database import db
from app.forecasting.service import LEGACY_OUTPUT_FIELDS as OUTPUT_FIELDS, run_forecast
from app.models.alerta_estoque import AlertaEstoque
from app.models.cliente import Cliente
from app.models.contexto_conversa import ContextoConversa
from app.models.historico_conversa import HistoricoConversa
from app.models.log_mensagem_agente import LogMensagemAgente
from app.models.movimentacao_estoque import MovimentacaoEstoque
from app.models.operacao_estoque import OperacaoEstoque
from app.models.produto import Produto
from app.models.venda import Venda
from app.services.estoque_service import EstoqueError


CATALOG = (
    ("caixa de 10L", "morango", 101), ("caixa de 10L", "chocolate", 102),
    ("caixa de 10L", "baunilha", 100), ("caixa de 5L", "morango", 66),
    ("caixa de 5L", "chocolate", 67), ("caixa de 5L", "baunilha", 65),
    ("caixa de sundae", "morango", 45), ("caixa de sundae", "chocolate", 48),
    ("caixa de picole", "caixa A", 120), ("caixa de picole", "caixa B", 120),
    ("caixa de picole", "caixa C", 120),
)


def _require(condition):
    # Continua verificando as condições mesmo quando a CLI usa python -O.
    if not condition:
        raise AssertionError("Condição experimental não satisfeita.")


class _Recorder:
    def __init__(self):
        self.checks = []
        self.timings = []

    def run(self, name, operation, *, scope=None):
        started = time.monotonic()
        try:
            result = operation()
        except Exception:
            # Exceções de transporte, SQL e caminhos nunca entram no relatório.
            self.checks.append({"id": name, "status": "falhou",
                                "detail": "A verificação não foi satisfeita."})
            return None
        else:
            self.checks.append({"id": name, "status": "aprovado"})
            return result
        finally:
            if scope is not None:
                self.timings.append({"name": name, "scope": scope,
                                     "seconds": time.monotonic() - started})


def _write(path, text):
    with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600),
                   "w", encoding="utf-8", newline="") as handle:
        handle.write(text)


def _prepare(workspace):
    """Constrói uma fonte marcada como fictícia; não usa fixtures do pytest."""
    if workspace.is_symlink() or not workspace.is_dir():
        raise ValueError("Informe uma pasta existente sem links simbólicos.")
    workspace = workspace.resolve()
    root = workspace / "functional"
    root.mkdir(mode=0o700)  # Uma repetição nunca sobrescreve a evidência anterior.
    subprocess.run(["git", "init", "--quiet", str(root)], check=True,
                   capture_output=True)
    _write(root / ".gitignore", "/data/\n")
    for relative in ("data", "data/processed", "data/reports", "data/artifacts"):
        (root / relative).mkdir(mode=0o700)
    today = datetime.now(ZoneInfo("America/Manaus")).date()
    cutoff, first = today - timedelta(days=1), today - timedelta(days=22)
    output = io.StringIO(newline="")
    writer = csv.writer(output, lineterminator="\n")
    writer.writerow(OUTPUT_FIELDS)
    for offset in range((cutoff - first).days + 1):
        writer.writerow(((first + timedelta(days=offset)).isoformat(),
                         "FICTICIO-001", "GRUPO-FICTICIO", "Categoria fictícia", 3, 1))
    content = output.getvalue()
    digest = sha256(content.encode()).hexdigest()
    for name in ("canonical.csv", "reviewed.csv"):
        _write(root / "data/processed" / name, content)
    preparation = {"status": "aprovado", "versao_pipeline": "2", "erros": {},
                   "politica_data": "dia_fonte", "colunas_dataset": list(OUTPUT_FIELDS),
                   "dataset": "data/processed/canonical.csv", "sha256_dataset": digest}
    _write(root / "data/reports/preparacao-ficticia.json", json.dumps(preparation))
    review = {"status": "revisado_com_recorte", "politica_data": "dia_fonte",
              "colunas": list(OUTPUT_FIELDS), "contexto": {"exportacao_completa": True},
              "data_referencia": cutoff.isoformat(),
              "relatorio_preparacao": "data/reports/preparacao-ficticia.json",
              "dataset_canonico": preparation["dataset"], "sha256_canonico": digest,
              "dataset_revisado": "data/processed/reviewed.csv", "sha256_revisado": digest}
    review_path = root / "data/reports/revisao-ficticia.json"
    _write(review_path, json.dumps(review))
    paths = run_forecast(root, review_path, cutoff=cutoff, start=first, horizon=7)
    database_path = root / "data/artifacts/functional.db"
    _write(database_path, "")
    application = create_app({"TESTING": True,
        "SQLALCHEMY_DATABASE_URI": f"sqlite:///{database_path}",
        "FORECAST_PROJECT_ROOT": root, "FORECAST_MANIFEST": str(paths["manifest"]),
        "FORECAST_REVIEW": str(review_path), "FORECAST_START": first.isoformat(),
        "PREDICTIVE_ALERTS_AUTO_ENABLED": False, "PREDICTIVE_ALERT_HORIZON_DAYS": 7})
    with application.app_context():
        db.create_all()
        products = [Produto(nome=f"{category} - {flavour}", categoria=category,
                            sabor=flavour, preco=price, quantidade_disponivel=20,
                            codigo_externo="FICTICIO-001" if flavour == "chocolate"
                                and category == "caixa de 10L" else None)
                    for category, flavour, price in CATALOG]
        db.session.add_all(products)
        db.session.add(Cliente(nome="Cliente Simulado"))
        db.session.commit()
        catalog = {product.nome: product.id for product in products}
        from app.services.catalogacao_service import _review
        report = json.loads(paths["report"].read_text())
        metadata = {code: {"categorias": [item["categoria"]] if item.get("categoria") else [],
                           "grupos": [item["codigo_grupo"]] if item.get("codigo_grupo") else []}
                    for code, item in report["dataset"]["products"].items()}
        for product in products:
            if product.codigo_externo:
                _review(db.session, product, metadata, "Operador fictício", "Catálogo sintético do experimento conferido.")
        db.session.commit()
    return application, catalog, today.isoformat()


def _ports():
    with socket.socket() as first, socket.socket() as second:
        first.bind(("127.0.0.1", 0))
        second.bind(("127.0.0.1", 0))
        return first.getsockname()[1], second.getsockname()[1]


class _Cases:
    def __init__(self, app, runtime, catalog, recorder):
        self.app, self.runtime, self.catalog, self.recorder = app, runtime, catalog, recorder
        self.gateway, self.client = AgentGateway(runtime), app.test_client()
        self.product = catalog["caixa de 10L - chocolate"]

    def reset(self):
        # Somente a fixture fictícia é restaurada. A auditoria XMPP é preservada.
        with self.app.app_context():
            for model in (MovimentacaoEstoque, OperacaoEstoque, Venda, ContextoConversa,
                          HistoricoConversa, AlertaEstoque):
                model.query.delete()
            for product in Produto.query.all():
                product.quantidade_disponivel, product.estoque_minimo = 20, 0
            db.session.commit()

    def chat(self, text):
        response = self.client.post("/api/atendimento", json={
            "mensagem": text, "cliente_nome": "Cliente Simulado"})
        _require(response.status_code == 200)
        return response.get_json()["resposta_agente"]

    def stock(self):
        return self.gateway.executar_estoque("consultar_produto", produto_id=self.product)["disponivel"]

    def move(self, kind, amount, *, key=None, item_type="produto", item=None):
        return self.gateway.executar_estoque("movimentar", tipo_item=item_type,
            item_id=item or self.product, tipo_movimentacao=kind, quantidade=amount,
            motivo="Cenário inteiramente fictício de validação.", operacao_id=key)

    def snapshot(self):
        with self.app.app_context():
            return (db.session.get(Produto, self.product).quantidade_disponivel,
                    Venda.query.count(), MovimentacaoEstoque.query.count())

    def no_order(self):
        with self.app.app_context():
            _require(Venda.query.count() == ContextoConversa.query.count() == 0)

    def tcc01(self):
        response = self.chat("quais produtos tem?")
        _require(response.startswith("Produtos disponíveis por categoria:"))
        for category, flavour, _ in CATALOG:
            _require(category in response and f"- {flavour}\n" in response)
        self.no_order()
        with self.app.app_context():
            _require(HistoricoConversa.query.one().resposta_agente == response)

    def tcc02(self):
        _require(self.chat("Tem caixa de 10L de chocolate?") ==
                 "Sim, temos caixa de 10L - chocolate disponível.")
        self.no_order()

    def tcc03(self):
        _require("Total: R$ 270.00" in self.chat(
            "quero 2 caixas de 10L chocolate e 1 caixa de 5L morango"))
        with self.app.app_context():
            _require(ContextoConversa.query.one().etapa == "aguardando_confirmacao")
            _require(Venda.query.count() == 0)
        _require("Total do pedido: R$ 270.00" in self.chat("sim"))
        with self.app.app_context():
            sales = Venda.query.order_by(Venda.id).all()
            _require([(s.produto_id, s.quantidade, s.valor_total) for s in sales] == [
                (self.product, 2, 204.0), (self.catalog["caixa de 5L - morango"], 1, 66.0)])
            _require(ContextoConversa.query.count() == 0)
            _require(db.session.get(Produto, self.product).quantidade_disponivel == 18)
            _require(db.session.get(Produto, self.catalog["caixa de 5L - morango"]).quantidade_disponivel == 19)

    def tcc04(self):
        response = self.chat("quero 1 caixa de 10L de chocolate, morango e baunilha")
        _require("Total: R$ 303.00" in response)
        _require("Total do pedido: R$ 303.00" in self.chat("sim"))
        with self.app.app_context():
            _require(Venda.query.count() == 3 and ContextoConversa.query.count() == 0)
            _require(all(p.quantidade_disponivel == (19 if p.categoria == "caixa de 10L" else 20)
                         for p in Produto.query.all()))

    def tcc05(self):
        _require("No momento não temos caixa de 10L - chocolate em estoque." in
                 self.chat("quero 1 caixa de 10L chocolate"))
        self.no_order()

    def tcc06(self):
        _require("A quantidade solicitada de caixa de 10L - chocolate não está disponível." in
                 self.chat("quero 21 caixas de 10L chocolate"))
        self.no_order()
        _require(self.stock() == 20)

    def tcc07(self):
        _require(self.chat("qual o preço da caixa de 10L chocolate?") ==
                 "O valor de caixa de 10L - chocolate é R$ 102.00.")
        self.no_order()

    def tcc08(self):
        _require(self.chat("preço da caixa de 10L chocolate e caixa de 5L morango") ==
            "Valores dos produtos solicitados:\n- caixa de 10L - chocolate: R$ 102.00\n"
            "- caixa de 5L - morango: R$ 66.00\n")
        self.no_order()

    def entrada(self):
        result = self.move("entrada", 5)
        _require(result["alterado"] and self.stock() == 25)

    def saida(self):
        result = self.move("saida", 3)
        _require(result["alterado"] and self.stock() == 22)

    def ajuste(self):
        result = self.move("ajuste", 9)
        _require(result["alterado"] and self.stock() == 9)
        with self.app.app_context():
            movement = db.session.get(MovimentacaoEstoque, result["movimentacao_id"])
            _require((movement.quantidade, movement.saldo_anterior, movement.saldo_posterior) == (13, 22, 9))

    def venda(self):
        before = self.snapshot()
        response = self.gateway.baixar_estoque([
            {"produto_id": self.product, "quantidade": 1},
            {"produto_id": self.product, "quantidade": 2}], "Cliente Simulado")
        _require(response.payload["success"] and len(response.payload["vendas"]) == 1)
        _require(self.snapshot() == (6, before[1] + 1, before[2] + 1))

    def zerado(self):
        response = self.gateway.consultar_disponibilidade([
            {"produto_id": self.product, "quantidade": 1}])
        _require(not response.payload["available"] and self.stock() == 0)

    def insuficiente(self):
        before = self.snapshot()
        try:
            self.gateway.baixar_estoque([
                {"produto_id": self.product, "quantidade": before[0] + 1}], "Cliente Simulado")
        except RemoteFailure as error:
            _require(error.code == "INSUFFICIENT_STOCK")
        else:
            _require(False)
        _require(self.snapshot() == before)

    def minimo(self):
        for _ in range(2):
            self.gateway.executar_estoque("configurar_minimo", tipo_item="produto",
                                         item_id=self.product, estoque_minimo=6)
        with self.app.app_context():
            rows = AlertaEstoque.query.filter_by(produto_id=self.product,
                tipo_alerta="estoque_minimo", ativo=True).all()
            _require(len(rows) == 1 and self.stock() == 6)

    def recuperacao(self):
        self.move("entrada", 1)
        with self.app.app_context():
            rows = AlertaEstoque.query.filter_by(produto_id=self.product,
                tipo_alerta="estoque_minimo").all()
            _require(rows and all(not row.ativo and row.resolvido_em is not None for row in rows))
            _require(self.stock() == 7)

    def deduplicacao(self):
        key, before = str(uuid4()), self.snapshot()
        items = [{"produto_id": self.product, "quantidade": 1}]
        first = self.gateway.baixar_estoque(items, "Cliente Simulado", key)
        repeat = self.gateway.baixar_estoque(items, "Cliente Simulado", key)
        _require(first.payload == repeat.payload and first.thread != repeat.thread)
        _require(self.snapshot() == (before[0] - 1, before[1] + 1, before[2] + 1))

    def materia_prima(self):
        material = self.gateway.executar_estoque("cadastrar_materia_prima",
            codigo="INSUMO-FICTICIO", nome="Insumo fictício", unidade_medida="kg", estoque_minimo="1")
        before = self.snapshot()
        self.move("entrada", "2.75", item_type="materia_prima", item=material["id"])
        self.move("saida", "0.25", item_type="materia_prima", item=material["id"])
        self.move("ajuste", "1", item_type="materia_prima", item=material["id"])
        result = self.gateway.executar_estoque("visao_estoque")
        _require(result["materias_primas"][0]["quantidade_disponivel"] == "1.000000000")
        _require(any(a["tipo_item"] == "materia_prima" and a["ativo"] for a in result["alertas"]))
        _require(self.snapshot() == (before[0], before[1], before[2] + 3))

    def preventivo(self):
        demand = Decimal(self.gateway.consultar_previsao(self.product).payload["total_previsto"])
        _require(demand > 1 and demand == int(demand))
        self.gateway.executar_estoque("configurar_minimo", tipo_item="produto",
                                     item_id=self.product, estoque_minimo=1)
        # Estoque suficiente hoje: o risco decorre da demanda futura.
        self.move("ajuste", int(demand))
        with self.app.app_context():
            _require(AlertaEstoque.query.filter_by(produto_id=self.product,
                tipo_alerta="estoque_minimo", ativo=True).count() == 0)
        first = self.gateway.sincronizar_alerta_preventivo(self.product)
        repeat = self.gateway.sincronizar_alerta_preventivo(self.product)
        _require(first["risco"] and first["acao"] == "criado")
        _require(first["disponivel"] > first["estoque_minimo"] == 1
                 and first["saldo_projetado"] == "0")
        _require(repeat["acao"] == "atualizado" and first["alerta_id"] == repeat["alerta_id"])
        with self.app.app_context():
            _require(AlertaEstoque.query.filter_by(tipo_alerta="risco_futuro", ativo=True).count() == 1)

    def horizontes(self):
        with self.app.app_context():
            row = AlertaEstoque.query.filter_by(tipo_alerta="risco_futuro", ativo=True).one()
            before = (row.id, row.atualizado_em, json.dumps(row.previsao_json, sort_keys=True))
        self.gateway.consultar_risco(self.product, horizonte_dias=1)
        try:
            self.gateway.sincronizar_alerta_preventivo(self.product, 1)
        except EstoqueError as error:
            _require(error.code == "ALERT_HORIZON_MISMATCH")
        else:
            _require(False)
        with self.app.app_context():
            row = AlertaEstoque.query.filter_by(tipo_alerta="risco_futuro", ativo=True).one()
            _require((row.id, row.atualizado_em, json.dumps(row.previsao_json, sort_keys=True)) == before)

    def igualdade_preventiva(self):
        demand = Decimal(self.gateway.consultar_previsao(self.product).payload["total_previsto"])
        _require(demand == int(demand))
        self.move("ajuste", int(demand) + 1)
        result = self.gateway.sincronizar_alerta_preventivo(self.product)
        _require(result["saldo_projetado"] == "1" and not result["risco"])
        _require(result["acao"] == "resolvido")

    def execute(self):
        for number in range(1, 9):
            self.reset()
            if number == 5:
                self.move("ajuste", 0)
            self.recorder.run(f"atendimento.tcc_i_{number:02}", getattr(self, f"tcc{number:02}"),
                              scope="http_atendimento_caso_completo")
        self.reset()
        for name in ("entrada", "saida", "ajuste", "venda", "insuficiente"):
            self.recorder.run(f"estoque.{name}", getattr(self, name), scope="gateway_caso_completo")
        self.move("ajuste", 0)
        self.recorder.run("estoque.zerado", self.zerado, scope="gateway_caso_completo")
        self.recorder.run("estoque.zerado_rejeita_venda", self.insuficiente, scope="gateway_caso_completo")
        self.move("ajuste", 6)
        for name in ("minimo", "recuperacao", "deduplicacao", "materia_prima", "preventivo",
                     "horizontes", "igualdade_preventiva"):
            self.recorder.run(f"estoque.{name}", getattr(self, name), scope="gateway_caso_completo")


def _communication(app):
    with app.app_context():
        rows = LogMensagemAgente.query.all()
        counts = Counter((row.ontology, row.performative, row.status) for row in rows)
        _require(all(row.payload == {"redacted": True} for row in rows))
        exchanges = {}
        for row in rows:
            exchanges.setdefault(row.thread, []).append(row)
        for exchange in exchanges.values():
            _require(len({row.ontology for row in exchange}) == 1)
            _require(Counter((row.performative, row.status) for row in exchange) in (
                Counter({("request", "sent"): 1, ("request", "received"): 1,
                         ("inform", "sent"): 1, ("inform", "received"): 1}),
                Counter({("request", "sent"): 1, ("request", "received"): 1,
                         ("failure", "sent"): 1, ("failure", "received"): 1})))
            request = next(row for row in exchange
                           if row.performative == "request" and row.status == "sent")
            response = next(row for row in exchange
                            if row.performative != "request" and row.status == "sent")
            _require(request.sender == response.receiver and request.receiver == response.sender)
            _require(all(row.sender == request.sender and row.receiver == request.receiver
                         for row in exchange if row.performative == "request"))
            _require(all(row.sender == response.sender and row.receiver == response.receiver
                         for row in exchange if row.performative != "request"))
        groups = sorted({(ontology, performative) for ontology, performative, _ in counts})
        return {"transport": "xmpp_localhost", "exchanges": len(exchanges),
            "sent": sum(count for (_, _, status), count in counts.items() if status == "sent"),
            "received": sum(count for (_, _, status), count in counts.items() if status == "received"),
            "failure_sent": sum(count for (_, performative, status), count in counts.items()
                                if performative == "failure" and status == "sent"),
            "failure_received": sum(count for (_, performative, status), count in counts.items()
                                    if performative == "failure" and status == "received"),
            "failure_semantics": "Rejeições de negócio esperadas; não são falhas de transporte.",
            "counts": [{"ontology": ontology, "performative": performative,
                        "status": status, "count": count}
                       for (ontology, performative, status), count in sorted(counts.items())],
            "by_ontology_performative": [{"ontology": ontology, "performative": performative,
                "sent": counts[(ontology, performative, "sent")],
                "received": counts[(ontology, performative, "received")]}
                for ontology, performative in groups]}


def run_functional_experiment(workspace: Path) -> dict:
    """Executa condições verificáveis, encerra recursos e retorna evidência segura.

    Uma latência representa um caso completo (que pode conter várias chamadas),
    em uma única execução controlada. Não é benchmark de carga nem percentil de
    RPCs individuais. Falhas não incluem texto de exceções, payloads ou segredos.
    """
    recorder, application, runtime, reference = _Recorder(), None, None, None
    communication = None
    prepared = recorder.run("infraestrutura.preparacao_ficticia", lambda: _prepare(Path(workspace)))
    try:
        if prepared is not None:
            application, catalog, reference = prepared
            ports = _ports()
            runtime = AgentRuntime(application, RuntimeConfig(
                {name: secrets.token_urlsafe(24) for name in JIDS}, *ports))
            recorder.run("infraestrutura.inicializacao_xmpp", runtime.start)
            if runtime.running:
                application.extensions["agent_runtime"] = runtime
                application.extensions["agent_gateway"] = AgentGateway(runtime)
                runtime.call(runtime.agents["previsao"].aguardar_inicializacao)
                def health():
                    value = runtime.health()
                    _require(value["running"] and set(value["agents"]) == set(JIDS)
                             and all(value["agents"].values()))
                recorder.run("comunicacao.tres_agentes_online", health)
                gateway = AgentGateway(runtime)
                for source, target in (("atendimento", "estoque"), ("estoque", "atendimento"),
                                       ("estoque", "previsao"), ("previsao", "estoque")):
                    def ping(source=source, target=target):
                        reply = gateway.ping(source, target)
                        _require(reply.payload == {"agent": target, "status": "ok"})
                    recorder.run(f"comunicacao.{source}_para_{target}", ping)
                _Cases(application, runtime, catalog, recorder).execute()
                # Uma resposta pode chegar antes de o remetente persistir seu
                # metadado sent. Aguarda quiescência verificável, não um tempo fixo.
                deadline = time.monotonic() + 5
                while True:
                    try:
                        communication = _communication(application)
                        break
                    except AssertionError:
                        if time.monotonic() >= deadline:
                            raise
                        time.sleep(0.02)
    except Exception:
        recorder.run("infraestrutura.execucao_completa", lambda: _require(False))
    finally:
        if runtime is not None:
            def shutdown():
                runtime.stop()
                _require(not runtime.thread or not runtime.thread.is_alive())
                _require(not runtime.server or not runtime.server.is_alive())
                _require(not any(agent.is_alive() for agent in runtime.agents.values()))
            recorder.run("infraestrutura.encerramento", shutdown)
        if application is not None:
            def audit():
                nonlocal communication
                communication = _communication(application)
                _require(communication["sent"] > 0 and communication["failure_sent"] == 2)
            recorder.run("comunicacao.auditoria_correlacionada_redigida", audit)
            with application.app_context():
                db.session.remove()
                db.engine.dispose()
    return {"status": "aprovado" if all(c["status"] == "aprovado" for c in recorder.checks) else "falhou",
            "data_origin": "inteiramente_ficticia", "reference_date": reference,
            "checks": recorder.checks, "communication": communication, "timings": recorder.timings,
            "timing_scope": "Um caso completo por amostra; exclui fixture, startup e warmup.",
            "repetitions": 1, "persistent_alert_horizon_days": 7,
            "production_database_used": False}
