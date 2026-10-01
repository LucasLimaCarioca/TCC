"""Preparação local de demanda; nenhum registro pessoal sai deste pipeline."""

from collections import Counter, defaultdict
import csv
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, localcontext
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
from uuid import uuid4
from zoneinfo import ZoneInfo


REQUIRED_FIELDS = ("pvNro", "proCodCli", "proNomeunificado", "pvEmis", "pvQtd")
OPTIONAL_FIELDS = ("gproNro", "gproNm", "pvPrcunt", "pvPrctot")
OUTPUT_FIELDS = ("data", "codigo_produto", "codigo_grupo", "categoria", "quantidade", "numero_pedidos")
DATE_POLICIES = ("dia_fonte", "America/Manaus")
PIPELINE_VERSION = "2"


class PreparationError(ValueError):
    """Erro seguro para a CLI: nunca inclui valores da fonte."""


def _git(root, *args):
    result = subprocess.run(
        ["git", "-C", str(root), *args], capture_output=True, check=False,
    )
    return result


def private_path(root, candidate):
    """Exige caminho real dentro de data/, ignorado e não rastreado pelo Git."""
    root = Path(root).resolve()
    candidate = Path(candidate)
    if not candidate.is_absolute():
        candidate = root / candidate
    # Rejeita links inclusive nos diretórios ancestrais, antes de resolve().
    try:
        relative = candidate.relative_to(root)
    except ValueError:
        raise PreparationError("O caminho deve permanecer na pasta data/ do projeto.") from None
    cursor = root
    for part in relative.parts:
        cursor = cursor / part
        if cursor.is_symlink():
            raise PreparationError("Links simbólicos não são permitidos para dados históricos.")
    resolved = candidate.resolve()
    try:
        resolved.relative_to(root / "data")
    except ValueError:
        raise PreparationError("O caminho deve permanecer na pasta data/ do projeto.") from None
    pathspec = resolved.relative_to(root).as_posix()
    ignored = _git(root, "check-ignore", "--quiet", "--no-index", "--", pathspec)
    tracked = _git(root, "ls-files", "--cached", "-z", "--", pathspec)
    if ignored.returncode != 0 or tracked.returncode != 0 or tracked.stdout:
        raise PreparationError("Os caminhos de dados devem estar ignorados e fora do índice Git.")
    return resolved


def _write_private(path, content):
    # Arquivos novos somente; nunca sobrescreve o original ou resultados anteriores.
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
        handle.write(content)


def _decimal(value):
    try:
        number = Decimal(value)
    except InvalidOperation:
        raise ValueError from None
    # Limita escala/magnitude para evitar expansão descontrolada de entradas inválidas.
    if not number.is_finite() or number.copy_abs() > Decimal("1e15") or number.as_tuple().exponent < -9:
        raise ValueError
    return number


def _day(value, policy):
    try:
        moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if moment.tzinfo is None or moment.utcoffset() is None:
            raise ValueError
        if policy == "America/Manaus":
            moment = moment.astimezone(ZoneInfo("America/Manaus"))
        return moment.date().isoformat()
    except (ValueError, OverflowError):
        raise ValueError from None


def _number_text(number):
    return format(number, "f")


def prepare_history(root, source="data/pedido.csv", *, date_policy):
    """Retorna caminhos privados e status; inconsistências bloqueiam o dataset.

    A política de data deve ser explícita. Linhas inválidas não são descartadas
    silenciosamente: o relatório é gerado e nenhum dataset parcial é liberado.
    """
    if date_policy not in DATE_POLICIES:
        raise PreparationError("Escolha uma política de data suportada explicitamente.")
    root = Path(root).resolve()
    source_path = private_path(root, source)
    processed = private_path(root, "data/processed")
    reports = private_path(root, "data/reports")
    if not source_path.is_file():
        raise PreparationError("O arquivo de entrada não foi encontrado.")
    if source_path.stat().st_nlink != 1:
        raise PreparationError("O arquivo de entrada não pode ter vínculos físicos adicionais.")
    try:
        source_bytes = source_path.read_bytes()
        source_text = source_bytes.decode("utf-8-sig")
    except (OSError, UnicodeError):
        raise PreparationError("Não foi possível ler a fonte como CSV UTF-8.") from None

    report = {
        "versao_pipeline": PIPELINE_VERSION,
        "gerado_em": datetime.now(timezone.utc).isoformat(),
        "sha256_fonte": hashlib.sha256(source_bytes).hexdigest(),
        "politica_data": date_policy,
        "status": "bloqueado",
        "linhas_lidas": 0,
        "linhas_validas": 0,
        "linhas_invalidas": 0,
        "duplicatas_exatas": 0,
        "quantidades_negativas": 0,
        "quantidades_zero": 0,
        "campos_obrigatorios_ausentes": [],
        "valores_ausentes": dict.fromkeys(REQUIRED_FIELDS + OPTIONAL_FIELDS, 0),
        "erros": {},
        "inconsistencias": {},
    }
    errors = Counter()
    seen = set()
    totals = defaultdict(Decimal)
    orders = defaultdict(set)
    product_names = defaultdict(set)
    product_groups = defaultdict(set)
    product_categories = defaultdict(set)
    group_names = defaultdict(set)
    daily_groups = defaultdict(set)
    daily_categories = defaultdict(set)
    product_days = defaultdict(set)
    product_codes = set()
    order_codes = set()
    days = set()

    try:
        reader = csv.reader(io.StringIO(source_text, newline=""), strict=True)
        headers = next(reader, [])
        report["campos_obrigatorios_ausentes"] = sorted(set(REQUIRED_FIELDS) - set(headers))
        if report["campos_obrigatorios_ausentes"]:
            errors["schema_campos_ausentes"] += 1
        if len(set(headers)) != len(headers):
            errors["schema_cabecalho_duplicado"] += 1
        if not errors:
            # Os campos pessoais e desconhecidos nunca são projetados na saída.
            for values in reader:
                report["linhas_lidas"] += 1
                if len(values) != len(headers):
                    errors["estrutura_linha"] += 1
                    report["linhas_invalidas"] += 1
                    continue
                signature = tuple(values)
                if signature in seen:
                    report["duplicatas_exatas"] += 1
                seen.add(signature)
                row = dict(zip(headers, values))
                row_errors = set()
                for field in REQUIRED_FIELDS + OPTIONAL_FIELDS:
                    if not row.get(field, "").strip():
                        report["valores_ausentes"][field] += 1
                        if field in REQUIRED_FIELDS:
                            row_errors.add("campo_obrigatorio_vazio")
                try:
                    day = _day(row["pvEmis"].strip(), date_policy)
                except ValueError:
                    row_errors.add("data_invalida_ou_sem_fuso")
                try:
                    quantity = _decimal(row["pvQtd"].strip())
                    if quantity < 0:
                        report["quantidades_negativas"] += 1
                        row_errors.add("quantidade_negativa")
                    elif quantity == 0:
                        report["quantidades_zero"] += 1
                except ValueError:
                    row_errors.add("quantidade_invalida")
                for field in ("pvPrcunt", "pvPrctot"):
                    value = row.get(field, "").strip()
                    if value:
                        try:
                            if _decimal(value) < 0:
                                row_errors.add("valor_comercial_negativo")
                        except ValueError:
                            row_errors.add("valor_comercial_invalido")
                if row_errors:
                    report["linhas_invalidas"] += 1
                    errors.update(row_errors)
                    continue
                report["linhas_validas"] += 1
                code = row["proCodCli"].strip()
                order = row["pvNro"].strip()
                key = (day, code)
                with localcontext() as context:
                    context.prec = 50
                    totals[key] += quantity
                orders[key].add(order)
                product_names[code].add(row["proNomeunificado"].strip())
                group = row.get("gproNro", "").strip()
                category = row.get("gproNm", "").strip()
                if group:
                    product_groups[code].add(group)
                    daily_groups[key].add(group)
                if category:
                    product_categories[code].add(category)
                    daily_categories[key].add(category)
                    if group:
                        group_names[group].add(category)
                product_days[code].add(day)
                product_codes.add(code)
                order_codes.add(order)
                days.add(day)
    except csv.Error:
        errors["csv_malformado"] += 1

    if not report["linhas_lidas"]:
        errors["sem_registros"] += 1
    name_conflicts = sum(len(names) > 1 for names in product_names.values())
    group_conflicts = sum(len(groups) > 1 for groups in product_groups.values())
    category_conflicts = sum(len(names) > 1 for names in product_categories.values())
    group_name_conflicts = sum(len(names) > 1 for names in group_names.values())
    report["inconsistencias"] = {
        "codigos_com_varios_nomes": name_conflicts,
        "codigos_com_varios_grupos": group_conflicts,
        "produtos_com_varias_categorias": category_conflicts,
        "grupos_com_varios_nomes": group_name_conflicts,
    }
    if report["duplicatas_exatas"]:
        errors["duplicatas_exigem_revisao"] += 1
    if name_conflicts or group_conflicts or category_conflicts or group_name_conflicts:
        errors["metadados_produto_exigem_revisao"] += 1
    report.update({
        "erros": dict(sorted(errors.items())),
        "produtos_encontrados": sorted(product_codes),
        "numero_produtos": len(product_codes),
        "numero_pedidos": len(order_codes),
        "intervalo_temporal": {"inicio": min(days) if days else None, "fim": max(days) if days else None},
        "numero_dias_observados": len(days),
        "numero_linhas_agregadas": len(totals),
        "dias_observados_por_produto": {code: len(observed) for code, observed in sorted(product_days.items())},
        "colunas_dataset": list(OUTPUT_FIELDS),
    })
    # Zero filling exige conhecer dias de operação e fica para a fase de séries.
    run_id = "preparacao-" + uuid4().hex
    reports.mkdir(mode=0o700, parents=True, exist_ok=True)
    report_path = private_path(root, reports / (run_id + ".json"))
    dataset_path = None
    if not errors:
        processed.mkdir(mode=0o700, parents=True, exist_ok=True)
        run_dir = private_path(root, processed / run_id)
        run_dir.mkdir(mode=0o700)
        dataset_path = private_path(root, run_dir / "demanda_diaria.csv")
        output = io.StringIO(newline="")
        writer = csv.writer(output, lineterminator="\n")
        writer.writerow(OUTPUT_FIELDS)
        for (day, code), quantity in sorted(totals.items()):
            key = (day, code)
            # Após validar conflitos, cada conjunto tem no máximo um valor.
            # Não infere metadados de outro produto ou de outro dia.
            group = next(iter(daily_groups[key]), "")
            category = next(iter(daily_categories[key]), "")
            writer.writerow((day, code, group, category, _number_text(quantity), len(orders[key])))
        dataset_text = output.getvalue()
        _write_private(dataset_path, dataset_text)
        report["status"] = "aprovado"
        report["sha256_dataset"] = hashlib.sha256(dataset_text.encode("utf-8")).hexdigest()
        report["dataset"] = dataset_path.relative_to(root).as_posix()
    try:
        _write_private(report_path, json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    except OSError:
        # Só remove o artefato que esta execução acabou de criar, nunca a fonte.
        if dataset_path is not None:
            dataset_path.unlink()
        raise PreparationError("Não foi possível salvar o relatório; dataset não liberado.") from None
    return {"status": report["status"], "dataset": dataset_path, "report": report_path}
