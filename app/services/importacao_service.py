"""Importação local do dataset diário aprovado; não lê pedidos brutos."""

from collections import Counter, defaultdict
import csv
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation, localcontext
import hashlib
import io
import json
from pathlib import Path
import re
from uuid import uuid4

from sqlalchemy import insert, select, text
from sqlalchemy.orm import Session

from app.database import db
from app.models.produto import Produto
from app.models.venda_historica import VendaHistorica
from app.services.preparacao_dados_service import (
    OUTPUT_FIELDS, PIPELINE_VERSION, PreparationError, _write_private, private_path,
)


IMPORT_VERSION = "1"


class ImportacaoError(ValueError):
    """Mensagem segura para a CLI; não contém dados ou exceções da fonte."""


def _input_file(root, candidate, directory):
    path = private_path(root, candidate)
    if not path.is_relative_to(root / "data" / directory):
        raise ImportacaoError("Use o diretório privado previsto para cada arquivo de entrada.")
    if not path.is_file() or path.stat().st_nlink != 1:
        raise ImportacaoError("Arquivo de entrada ausente ou com vínculos físicos adicionais.")
    return path


def _hash(value):
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def _quantity_text(value):
    if value == 0:
        return "0"
    value = format(value, "f")
    return value.rstrip("0").rstrip(".") if "." in value else value


def _canonical(row):
    return {
        "data": row["data"].isoformat(),
        "codigo_produto": row["codigo_produto"],
        "codigo_grupo": row["codigo_grupo"] or "",
        "categoria": row["categoria"] or "",
        "quantidade": _quantity_text(row["quantidade"]),
        "numero_pedidos": row["numero_pedidos"],
    }


def _signature(row):
    content = json.dumps(_canonical(row), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _parse_row(values):
    row = {key: value.strip() for key, value in zip(OUTPUT_FIELDS, values)}
    errors = set()
    try:
        day = date.fromisoformat(row["data"])
        if day.isoformat() != row["data"]:
            raise ValueError
    except ValueError:
        errors.add("data_invalida")
    for field, maximum, required in (("codigo_produto", 64, True), ("codigo_grupo", 64, False),
                                     ("categoria", 100, False)):
        value = row[field]
        if len(value) > maximum or (required and not value) or any(ord(char) < 32 for char in value):
            errors.add("metadado_invalido")
    try:
        quantity = Decimal(row["quantidade"])
        if (not quantity.is_finite() or quantity < 0 or quantity >= Decimal("1e16")
                or quantity.as_tuple().exponent < -9):
            raise ValueError
    except (ValueError, InvalidOperation):
        errors.add("quantidade_invalida")
    orders_text = row["numero_pedidos"]
    if not re.fullmatch(r"[0-9]{1,19}", orders_text) or int(orders_text) > 2**63 - 1:
        errors.add("numero_pedidos_invalido")
    if errors:
        return None, errors
    return {
        "data": day, "codigo_produto": row["codigo_produto"],
        "codigo_grupo": row["codigo_grupo"] or None, "categoria": row["categoria"] or None,
        "quantidade": quantity, "numero_pedidos": int(orders_text),
    }, errors


def _read_dataset(content, report, errors):
    rows = {}
    groups, categories, group_names = defaultdict(set), defaultdict(set), defaultdict(set)
    try:
        reader = csv.reader(io.StringIO(content.decode("utf-8-sig"), newline=""), strict=True)
        if tuple(next(reader, [])) != OUTPUT_FIELDS:
            errors["schema_incompativel"] += 1
            return rows
        for values in reader:
            report["linhas_lidas"] += 1
            if len(values) != len(OUTPUT_FIELDS):
                errors["estrutura_linha"] += 1
                report["linhas_rejeitadas"] += 1
                continue
            row, row_errors = _parse_row(values)
            if row_errors:
                errors.update(row_errors)
                report["linhas_rejeitadas"] += 1
                continue
            report["linhas_validas"] += 1
            key = row["codigo_produto"], row["data"]
            row["source_hash"] = _signature(row)
            if key in rows:
                if row["source_hash"] == rows[key]["source_hash"]:
                    report["duplicatas_no_dataset"] += 1
                else:
                    errors["observacoes_divergentes_no_dataset"] += 1
                continue
            rows[key] = row
            if row["codigo_grupo"]:
                groups[row["codigo_produto"]].add(row["codigo_grupo"])
            if row["categoria"]:
                categories[row["codigo_produto"]].add(row["categoria"])
                if row["codigo_grupo"]:
                    group_names[row["codigo_grupo"]].add(row["categoria"])
        if not report["linhas_lidas"]:
            errors["sem_registros"] += 1
        if any(len(values) > 1 for mapping in (groups, categories, group_names) for values in mapping.values()):
            errors["metadados_inconsistentes"] += 1
    except UnicodeError:
        errors["encoding_invalido"] += 1
    except csv.Error:
        errors["csv_malformado"] += 1
    return rows


def _profile(rows, report):
    per_product = defaultdict(list)
    for row in rows.values():
        per_product[row["codigo_produto"]].append(row)
    days = [row["data"] for row in rows.values()]
    report["intervalo_temporal"] = {
        "inicio": min(days).isoformat() if days else None,
        "fim": max(days).isoformat() if days else None,
    }
    report["numero_produtos"] = len(per_product)
    report["observacoes_unicas"] = len(rows)
    report["perfil_por_produto"] = {}
    for code, observations in sorted(per_product.items()):
        first, last = min(row["data"] for row in observations), max(row["data"] for row in observations)
        with localcontext() as context:
            context.prec = 50
            total = sum((row["quantidade"] for row in observations), Decimal(0))
        report["perfil_por_produto"][code] = {
            "primeiro_dia": first.isoformat(), "ultimo_dia": last.isoformat(),
            "dias_observados": len(observations),
            "dias_sem_observacao_no_intervalo": (last - first).days + 1 - len(observations),
            "dias_observados_com_quantidade_zero": sum(row["quantidade"] == 0 for row in observations),
            "quantidade_total": _quantity_text(total),
        }


def _save_report(root, report, errors):
    report["erros"] = dict(sorted(errors.items()))
    directory = private_path(root, "data/reports")
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    path = private_path(root, directory / ("importacao-" + uuid4().hex + ".json"))
    _write_private(path, json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    return path


def _stored_rows(session, codes):
    found = {}
    codes = sorted(codes)
    for start in range(0, len(codes), 500):
        records = session.scalars(select(VendaHistorica).where(
            VendaHistorica.codigo_produto.in_(codes[start:start + 500])
        ).execution_options(populate_existing=True)).all()
        for record in records:
            found[record.codigo_produto, record.data] = record
    return found


def _stored_signature(record):
    return _signature({field: getattr(record, field) for field in OUTPUT_FIELDS})


def importar_historico(project_root, preparation_report, *, validar_apenas=False):
    """Valida e importa tudo ou nada; sobreposições divergentes exigem revisão.

    O relatório da preparação vincula o CSV diário e a política de datas pelo
    hash. Os produtos sem mapeamento são preservados por código externo, sem
    criar produtos, alterar o catálogo ou movimentar estoque.
    """
    root = Path(project_root).resolve()
    try:
        input_report = _input_file(root, preparation_report, "reports")
        preparation = json.loads(input_report.read_text(encoding="utf-8"))
        if (not isinstance(preparation, dict) or preparation.get("status") != "aprovado"
                or preparation.get("versao_pipeline") != PIPELINE_VERSION
                or preparation.get("politica_data") != "dia_fonte"
                or preparation.get("erros") != {}
                or preparation.get("colunas_dataset") != list(OUTPUT_FIELDS)
                or not _hash(preparation.get("sha256_dataset"))
                or not _hash(preparation.get("sha256_fonte"))
                or not isinstance(preparation.get("dataset"), str)):
            raise ImportacaoError("Exige relatório aprovado da preparação atual, com política dia_fonte.")
        source = _input_file(root, preparation["dataset"], "processed")
        content = source.read_bytes()
        if db.engine.dialect.name != "sqlite":
            raise ImportacaoError("A importação desta fase suporta apenas SQLite local.")
        report = {
            "versao_importacao": IMPORT_VERSION,
            "versao_preparacao": PIPELINE_VERSION,
            "gerado_em": datetime.now(timezone.utc).isoformat(),
            "relatorio_preparacao": input_report.relative_to(root).as_posix(),
            "dataset": source.relative_to(root).as_posix(),
            "sha256_fonte": preparation["sha256_fonte"],
            "sha256_dataset": hashlib.sha256(content).hexdigest(),
            "politica_data": "dia_fonte", "colunas_dataset": list(OUTPUT_FIELDS),
            "modo": "validacao" if validar_apenas else "importacao", "status": "bloqueado",
            "linhas_lidas": 0, "linhas_validas": 0, "linhas_rejeitadas": 0,
            "duplicatas_no_dataset": 0, "linhas_ja_importadas": 0, "linhas_novas": 0,
            "linhas_inseridas": 0, "conflitos_com_banco": 0,
        }
        errors = Counter()
        if report["sha256_dataset"] != preparation["sha256_dataset"]:
            errors["hash_dataset_divergente"] += 1
            rows = {}
        else:
            rows = _read_dataset(content, report, errors)
        _profile(rows, report)
        if errors:
            return {"status": "bloqueado", "report": _save_report(root, report, errors)}

        path = None
        with Session(db.engine) as session:
            try:
                if not validar_apenas:
                    session.execute(text("BEGIN IMMEDIATE"))
                codes = {code for code, _ in rows}
                mapped = set(session.scalars(select(Produto.codigo_externo).where(
                    Produto.codigo_externo.is_not(None)
                )))
                report["produtos_sem_mapeamento"] = sorted(codes - mapped)
                report["produtos_mapeados"] = len(codes & mapped)
                existing = _stored_rows(session, codes)
                new = []
                for key, row in rows.items():
                    stored = existing.get(key)
                    if stored is None:
                        new.append({**row, "dataset_hash": report["sha256_dataset"]})
                    elif stored.source_hash == row["source_hash"] and _stored_signature(stored) == row["source_hash"]:
                        report["linhas_ja_importadas"] += 1
                    else:
                        report["conflitos_com_banco"] += 1
                report["linhas_novas"] = len(new)
                if report["conflitos_com_banco"]:
                    errors["observacoes_divergentes_no_banco"] = report["conflitos_com_banco"]
                if not errors and not validar_apenas:
                    for start in range(0, len(new), 500):
                        session.execute(insert(VendaHistorica), new[start:start + 500])
                    # SQLite/Numeric pode converter para float. Releitura evita
                    # confirmar valores cuja precisão foi perdida no armazenamento.
                    persisted = _stored_rows(session, codes)
                    if any(_stored_signature(persisted[key]) != row["source_hash"] for key, row in rows.items()):
                        errors["precisao_nao_preservada_no_sqlite"] += 1
                if errors:
                    session.rollback()
                else:
                    report["status"] = "validado" if validar_apenas else "importado"
                    report["linhas_inseridas"] = 0 if validar_apenas else len(new)
                # Falha de gravação do relatório ainda reverte as inserções.
                path = _save_report(root, report, errors)
                if not errors and not validar_apenas:
                    session.commit()
            except BaseException:
                session.rollback()
                if path is not None:
                    path.unlink()
                raise
        return {"status": report["status"], "report": path}
    except ImportacaoError:
        raise
    except PreparationError as error:
        raise ImportacaoError(str(error)) from None
    except Exception:
        # SQLAlchemy/CSV/JSON podem carregar valores nos erros: nunca expor.
        raise ImportacaoError("Falha local de leitura, validação ou gravação; importação não confirmada.") from None


def serie_diaria(codigo_produto, *, inicio=None, fim=None):
    """Reconstrói observações do banco; não preenche dias ausentes nem exporta."""
    if not isinstance(codigo_produto, str) or not codigo_produto.strip() or len(codigo_produto.strip()) > 64:
        raise ImportacaoError("Informe um código externo textual válido.")
    try:
        first = date.fromisoformat(inicio) if inicio is not None else None
        last = date.fromisoformat(fim) if fim is not None else None
        if (first and first.isoformat() != inicio) or (last and last.isoformat() != fim) or (first and last and first > last):
            raise ValueError
    except (ValueError, TypeError):
        raise ImportacaoError("Informe um intervalo válido em YYYY-MM-DD.") from None
    query = select(VendaHistorica).where(VendaHistorica.codigo_produto == codigo_produto.strip())
    if first:
        query = query.where(VendaHistorica.data >= first)
    if last:
        query = query.where(VendaHistorica.data <= last)
    with Session(db.engine) as session:
        return [_canonical({field: getattr(record, field) for field in OUTPUT_FIELDS})
                for record in session.scalars(query.order_by(VendaHistorica.data))]
