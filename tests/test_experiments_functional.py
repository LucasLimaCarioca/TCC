"""Validação do experimento com fontes fictícias e transporte real."""

import json
import subprocess
import sys

import pytest

from app.experiments import functional


def test_experimento_executa_casos_e_mede_transporte_real(tmp_path):
    result = functional.run_functional_experiment(tmp_path)
    failed = [row for row in result["checks"] if row["status"] != "aprovado"]
    assert failed == []
    assert result["status"] == "aprovado"
    assert result["data_origin"] == "inteiramente_ficticia"
    assert not result["production_database_used"]
    ids = {check["id"] for check in result["checks"]}
    assert {f"atendimento.tcc_i_{index:02}" for index in range(1, 9)} <= ids
    assert {"estoque.entrada", "estoque.saida", "estoque.ajuste", "estoque.venda",
            "estoque.zerado", "estoque.insuficiente", "estoque.minimo",
            "estoque.preventivo", "estoque.deduplicacao", "estoque.horizontes",
            "estoque.igualdade_preventiva", "estoque.materia_prima"} <= ids
    communication = result["communication"]
    assert communication["transport"] == "xmpp_localhost"
    assert communication["sent"] == communication["received"] > 0
    assert communication["failure_sent"] == communication["failure_received"] == 2
    assert communication["sent"] == 2 * communication["exchanges"]
    assert {row["ontology"] for row in communication["by_ontology_performative"]} >= {
        "estoque.disponibilidade", "estoque.baixa_venda", "previsao.consulta"}
    assert all(row["sent"] == row["received"] for row in communication["by_ontology_performative"])
    assert len(result["timings"]) == 22
    assert all(row["seconds"] >= 0 for row in result["timings"])
    report = json.dumps(result)
    assert "Cliente Simulado" not in report
    assert "FICTICIO-001" not in report
    assert "payload" not in report and "password" not in report
    assert (tmp_path / "functional/data/artifacts/functional.db").is_file()


def test_recorder_nao_publica_excecoes_ou_falsifica_sucesso():
    recorder = functional._Recorder()
    def fail():
        raise ValueError("SEGREDO_FICTICIO_QUE_NAO_DEVE_SAIR")
    assert recorder.run("caso.falha", fail, scope="gateway_caso_completo") is None
    assert recorder.checks[0]["status"] == "falhou"
    assert "SEGREDO" not in json.dumps(recorder.checks)
    assert recorder.timings[0]["seconds"] >= 0


def test_diretorio_existente_nao_sobrescreve_evidencia(tmp_path):
    target = tmp_path / "functional"
    target.mkdir()
    marker = target / "evidencia-anterior.txt"
    marker.write_text("marcador fictício")
    result = functional.run_functional_experiment(tmp_path)
    assert result["status"] == "falhou"
    assert result["communication"] is None
    assert marker.read_text() == "marcador fictício"
    assert result["checks"][0]["id"] == "infraestrutura.preparacao_ficticia"


def test_require_levanta_mesmo_sem_assert_python():
    response = subprocess.run([sys.executable, "-O", "-c",
        "from app.experiments.functional import _require; _require(False)"],
        capture_output=True)
    assert response.returncode != 0
    assert b"AssertionError" in response.stderr


def test_falha_de_cenario_encerra_runtime_e_nao_expoe_excecao(tmp_path, monkeypatch):
    def fail(self):
        raise RuntimeError("SEGREDO_FICTICIO_NA_EXCECAO")
    monkeypatch.setattr(functional._Cases, "execute", fail)
    result = functional.run_functional_experiment(tmp_path)
    assert result["status"] == "falhou"
    states = {row["id"]: row["status"] for row in result["checks"]}
    assert states["infraestrutura.execucao_completa"] == "falhou"
    assert states["infraestrutura.encerramento"] == "aprovado"
    assert "SEGREDO_FICTICIO" not in json.dumps(result)
    assert functional.AgentRuntime._ownership.acquire(blocking=False)
    functional.AgentRuntime._ownership.release()


def test_auditoria_rejeita_correlacao_fora_do_contrato(app):
    from app.database import db
    from app.models.log_mensagem_agente import LogMensagemAgente
    with app.app_context():
        for performative, status, ontology in (
                ("request", "sent", "estoque.disponibilidade"),
                ("request", "received", "estoque.disponibilidade"),
                ("inform", "sent", "previsao.consulta"),
                ("inform", "received", "previsao.consulta")):
            db.session.add(LogMensagemAgente(sender="ficticio-a@localhost",
                receiver="ficticio-b@localhost", performative=performative,
                ontology=ontology, thread="ficticia", payload={"redacted": True}, status=status))
        db.session.commit()
    with pytest.raises(AssertionError):
        functional._communication(app)
