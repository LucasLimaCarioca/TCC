"""Contrato puro da seleção experimental, sem acesso ao banco ou aos arquivos."""

from copy import deepcopy
from hashlib import sha256
import json
import re


CONTEXT_FIELDS = {"versao", "natureza", "origem_historico", "estoque",
                  "vendas_operacionais", "assinatura", "associacoes"}
ASSOCIATION_FIELDS = {"produto_id", "codigo_produto", "versao_revisao"}


def context_signature(value):
    """Assina somente o snapshot, preservando os códigos textuais integralmente."""
    if type(value) is not dict:
        raise ValueError("Seleção experimental inválida.")
    payload = {key: item for key, item in value.items() if key != "assinatura"}
    return sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False, allow_nan=False,
                             separators=(",", ":")).encode("utf-8")).hexdigest()


def validate_experimental_context(value, *, codes=None):
    """Valida a identidade da execução; nunca confirma equivalências comerciais.

    A confirmação de identidade, embalagem e unidade pertence ao catálogo e à
    auditoria. Este contrato registra quais revisões foram utilizadas por uma
    execução imutável, sem substituir códigos externos por IDs internos.
    """
    try:
        if type(value) is not dict or set(value) != CONTEXT_FIELDS:
            raise ValueError
        if (type(value["versao"]) is not int or value["versao"] != 1
                or value["natureza"] != "experimental" or value["origem_historico"] != "real"
                or value["estoque"] != "simulado" or value["vendas_operacionais"] != "simuladas"):
            raise ValueError
        associations = value["associacoes"]
        if type(associations) is not list or not 1 <= len(associations) <= 11:
            raise ValueError
        external_codes, product_ids = [], []
        for item in associations:
            if type(item) is not dict or set(item) != ASSOCIATION_FIELDS:
                raise ValueError
            code = item["codigo_produto"]
            if (type(item["produto_id"]) is not int or item["produto_id"] <= 0
                    or type(code) is not str or not 1 <= len(code) <= 64
                    or any(char.isspace() or ord(char) < 32 or ord(char) == 127 for char in code)
                    or type(item["versao_revisao"]) is not str
                    or re.fullmatch(r"[0-9a-f]{64}", item["versao_revisao"]) is None):
                raise ValueError
            external_codes.append(code)
            product_ids.append(item["produto_id"])
        if (external_codes != sorted(set(external_codes))
                or len(set(product_ids)) != len(product_ids)
                or (codes is not None and set(codes) != set(external_codes))
                or type(value["assinatura"]) is not str
                or re.fullmatch(r"[0-9a-f]{64}", value["assinatura"]) is None
                or value["assinatura"] != context_signature(value)):
            raise ValueError
        return deepcopy(value)
    except (ValueError, TypeError, KeyError, OverflowError):
        raise ValueError("Seleção experimental inválida ou divergente dos códigos previstos.") from None
