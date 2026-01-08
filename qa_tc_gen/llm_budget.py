# llm_budget.py
import os
import re

from .llm_context_config import (
    LLM_MAX_TOKENS,
    CHARS_PER_TOKEN,
    DROP_CONFLUENCE_IF_TOO_LARGE,
    MAX_TRUTH_CHARS,
    MAX_CONTEXT_CHARS,
    MAX_CONFLUENCE_CHARS,
    LOG_CONTEXT_DECISIONS,
)

from .prompts import system_contract_no_tables_inventory_and_json


# Reserva defensiva para overhead de mensajes/roles/serialización + variación chars->tokens
SAFETY_OVERHEAD_TOKENS = 450

# Fail-fast rate limit: si el backend pide esperar > umbral, aborta con código explícito.
# (Por defecto 15 min; configurable por env)
FAIL_FAST_RATE_LIMIT_SECONDS = int(os.getenv("FAIL_FAST_RATE_LIMIT_SECONDS", "900"))


def _log_context(msg: str):
    if LOG_CONTEXT_DECISIONS:
        print(f"INFO CONTEXT: {msg}", flush=True)


def clip_text(label: str, text: str, max_chars: int) -> str:
    if not text:
        return ""
    if max_chars <= 0:
        return ""
    if len(text) <= max_chars:
        return text
    head = text[: int(max_chars * 0.7)]
    tail = text[-int(max_chars * 0.25) :]
    return f"{head}\n\n[... RECORTADO ({label}) ...]\n\n{tail}"


def approx_tokens_from_chars(chars: int) -> int:
    # CHARS_PER_TOKEN típico ~4. Ajustable por config.
    if CHARS_PER_TOKEN <= 0:
        return chars  # fallback seguro
    return int(chars / CHARS_PER_TOKEN)


def estimate_system_tokens_initial() -> int:
    """
    Estima tokens del system prompt principal (el más grande).
    Importante: el 413 te venía de no reservar estos tokens.
    """
    sys_txt = system_contract_no_tables_inventory_and_json() or ""
    # +200 chars margen por wrappers/variación de serialización
    return approx_tokens_from_chars(len(sys_txt) + 200)


def build_llm_payload(truth_text: str, context_text: str, confluence_text: str) -> dict:
    """
    Construye el payload final para LLM con presupuestos y reglas:
    - truth/context/confluence tienen caps por chars.
    - Si excede presupuesto REAL del modelo (restando system prompt + overhead):
      1) drop confluence (si flag)
      2) recorta context
      3) recorta truth (último recurso)
    """
    # 1) Caps por bloque (chars)
    truth = clip_text("truth", truth_text or "", MAX_TRUTH_CHARS)
    context = clip_text("context", context_text or "", MAX_CONTEXT_CHARS)
    confluence = clip_text("confluence", confluence_text or "", MAX_CONFLUENCE_CHARS)

    # 2) Presupuesto REAL: modelo - system_tokens - overhead
    system_tokens_est = estimate_system_tokens_initial()
    available_user_tokens = max(0, LLM_MAX_TOKENS - system_tokens_est - SAFETY_OVERHEAD_TOKENS)
    available_user_chars = int(available_user_tokens * CHARS_PER_TOKEN)

    def total_chars(t: str, c: str, cf: str) -> int:
        # separadores + overhead textual del prompt user
        return len(t) + len(c) + len(cf) + 900  # margen mayor para “headers” del prompt

    def total_tokens(t: str, c: str, cf: str) -> int:
        return approx_tokens_from_chars(total_chars(t, c, cf))

    _log_context(
        f"Budget model={LLM_MAX_TOKENS} | system_tokens_est={system_tokens_est} "
        f"| overhead_tokens={SAFETY_OVERHEAD_TOKENS} | available_user_tokens={available_user_tokens}"
    )

    tok = total_tokens(truth, context, confluence)
    _log_context(f"User payload inicial ~tokens={tok} (chars_budget~{available_user_chars}).")

    dropped_confluence = False

    # 3) Si excede, drop confluence primero
    if tok > available_user_tokens and DROP_CONFLUENCE_IF_TOO_LARGE and confluence:
        confluence = ""
        dropped_confluence = True
        tok = total_tokens(truth, context, confluence)
        _log_context(f"Drop CONFLUENCE por tamaño. Nuevo ~tokens={tok}.")

    # 4) Si aún excede, hard clip con reparto (context primero; truth se preserva al máximo)
    if tok > available_user_tokens:
        target_chars = int(available_user_chars * 0.92)  # margen adicional
        if target_chars <= 0:
            # fallback ultra defensivo
            target_chars = int(LLM_MAX_TOKENS * CHARS_PER_TOKEN * 0.50)

        # Reparto cuando vamos justos: 70% truth, 30% context
        truth_budget = min(MAX_TRUTH_CHARS, int(target_chars * 0.70))
        ctx_budget = min(MAX_CONTEXT_CHARS, int(target_chars * 0.30))

        # Si truth ya es menor, cedemos a context
        if len(truth) < truth_budget:
            extra = truth_budget - len(truth)
            ctx_budget = min(MAX_CONTEXT_CHARS, ctx_budget + extra)

        truth = clip_text("truth_hard", truth, truth_budget)
        context = clip_text("context_hard", context, ctx_budget)

        tok = total_tokens(truth, context, confluence)
        _log_context(
            f"Hard clip aplicado. truth_budget={truth_budget} ctx_budget={ctx_budget} -> ~tokens={tok}."
        )

    return {
        "truth": truth,
        "context": context,
        "confluence": confluence,
        "dropped_confluence": dropped_confluence,
        "approx_tokens_user_payload": tok,
        "system_tokens_est": system_tokens_est,
        "available_user_tokens": available_user_tokens,
    }


def is_rate_limit_daily_error(text: str) -> bool:
    if not text:
        return False
    # Mensaje típico: "Rate limit of 100 per 86400s exceeded ..."
    t = text.lower()
    return ("per 86400s" in t) or ("userbymodelbyday" in t) or ("per day" in t)


def extract_wait_seconds_from_rate_limit(text: str) -> int | None:
    """
    Extrae "Please wait XXXXX seconds" del error, si existe.
    """
    if not text:
        return None
    m = re.search(r"Please wait\s+(\d+)\s+seconds", text, re.IGNORECASE)
    if not m:
        return None
    try:
        return int(m.group(1))
    except Exception:
        return None
