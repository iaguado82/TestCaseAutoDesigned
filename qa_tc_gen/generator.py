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

from .config import TARGET_PROJECT
from .utils_text import strip_html_tags, dump_raw_response
from .utils_ai_parse import (
    extract_analysis_and_json,
    extract_inventory_block,
    extract_total_inventory,
    missing_inventory_ids,
    normalize_scenarios_merge,
    validate_scenarios_coverage,
)
from .automation_quality import (
    compute_automation_label,
    append_automation_block_to_description,
    append_kpi_block_option_a,  # (mantengo import por compatibilidad; KPI está desactivado abajo)
)
from .jira_client import (
    get_issue,
    get_parent_epic_key,
    create_test_case,
    get_doc_link,
    get_epic_link_key,
    get_dependency_issue_keys,
    link_issues,
)
from .confluence_client import get_confluence_content
from .github_models_client import call_github_models
from .prompts import (
    system_contract_no_tables_inventory_and_json,
    system_contract_only_missing_json_no_tables,
)

from .utils_logging import log_scenario_sources
from .utils_postprocess import to_corporate_template


# ---------------------------
# Presupuestos / límites (no LLM)
# ---------------------------
MAX_TRUTH_BLOCK_CHARS_PER_ISSUE = 8000
MAX_REFERENCED_JIRA_TICKETS = 8
MAX_REFERENCED_JIRA_DESC_CHARS = 900
MAX_EPIC_CONFLUENCE_CHARS = 2500
MAX_COMPLETION_CONTEXT_CHARS = 3500
CONTEXT_REFERENCE_DEPTH = 1

MAX_TRUTH_ISSUES = 10

# Reserva defensiva para overhead de mensajes/roles/serialización + variación chars->tokens
# (si el system prompt es grande, esta reserva evita el 413 por “casi”)
SAFETY_OVERHEAD_TOKENS = 450


# ---------------------------
# Regex utilidades
# ---------------------------
JIRA_KEY_RE = re.compile(r"([A-Z][A-Z0-9]+-\d+)")
CONF_URL_RE = re.compile(r"https?://confluence\.tid\.es/[^\s\]\)\|\,\>\"\' ]+")


def normalize_jira_wiki(desc: str) -> str:
    if not desc:
        return ""
    s = desc.replace("\r\n", "\n").replace("\r", "\n").strip()
    s = re.sub(r"(?<!\n)(h1\.\s+)", r"\n\1", s)
    s = re.sub(r"\s*----\s*", r"\n----\n", s)
    s = re.sub(r"(?<!\n)\s(\*\s+)", r"\n\1", s)
    s = re.sub(r"(?<!\n)\s(#\s+Acción:)", r"\n\1", s)
    s = re.sub(r"(?<!\n)\nh1\.", r"\n\nh1.", s)
    s = re.sub(r"\n{3,}", "\n\n", s).strip()
    return s.lstrip("\n")


def _clip_text(label: str, text: str, max_chars: int) -> str:
    if not text:
        return ""
    if max_chars <= 0:
        return ""
    if len(text) <= max_chars:
        return text
    head = text[: int(max_chars * 0.7)]
    tail = text[-int(max_chars * 0.25) :]
    return f"{head}\n\n[... RECORTADO ({label}) ...]\n\n{tail}"


def _approx_tokens_from_chars(chars: int) -> int:
    # CHARS_PER_TOKEN típico ~4. Ajustable por config.
    if CHARS_PER_TOKEN <= 0:
        return chars  # fallback seguro
    return int(chars / CHARS_PER_TOKEN)


def _log_context(msg: str):
    if LOG_CONTEXT_DECISIONS:
        print(f"INFO CONTEXT: {msg}", flush=True)


def ask_inventory_and_initial_scenarios(prompt: str):
    system_content = system_contract_no_tables_inventory_and_json()
    messages = [
        {"role": "system", "content": system_content},
        {"role": "user", "content": prompt},
    ]
    return call_github_models(messages, temperature=0.2, timeout=180)


def ask_missing_scenarios(inventory_text: str, compact_context_prompt: str, missing_ids_batch):
    if not missing_ids_batch:
        return None

    system_content = system_contract_only_missing_json_no_tables()
    ids_csv = ", ".join(str(i) for i in missing_ids_batch)

    user_prompt = (
        "Necesito que completes escenarios de prueba faltantes basados en este inventario.\n\n"
        "INVENTARIO (referencia):\n"
        f"{inventory_text}\n\n"
        "CONTEXTO (compacto, fuente de verdad + apoyo):\n"
        f"{compact_context_prompt}\n\n"
        f"Devuelve SOLO los escenarios con inventory_id EXACTAMENTE en esta lista: {ids_csv}\n"
        "REGLAS CRÍTICAS:\n"
        "- Cada inventory_id de la lista debe aparecer EXACTAMENTE una vez.\n"
        "- NO devuelvas ningún inventory_id que no esté en la lista.\n"
    )

    messages = [
        {"role": "system", "content": system_content},
        {"role": "user", "content": user_prompt},
    ]
    return call_github_models(messages, temperature=0.2, timeout=180)


def _extract_jira_keys_and_conf_urls(text: str):
    if not text:
        return [], []
    keys = list(dict.fromkeys(JIRA_KEY_RE.findall(text)))
    conf_urls = list(dict.fromkeys(CONF_URL_RE.findall(text)))
    return keys, conf_urls


def _estimate_system_tokens_initial() -> int:
    """
    Estima tokens del system prompt principal (el más grande).
    Importante: el 413 te venía de no reservar estos tokens.
    """
    sys_txt = system_contract_no_tables_inventory_and_json() or ""
    # +100 chars de margen por wrappers/variación
    return _approx_tokens_from_chars(len(sys_txt) + 100)


def build_llm_payload(truth_text: str, context_text: str, confluence_text: str) -> dict:
    """
    Construye el payload final para LLM con presupuestos y reglas:
    - truth/context/confluence tienen caps por chars.
    - Si excede presupuesto REAL del modelo (restando system prompt + overhead):
      1) drop confluence (si flag)
      2) recorta context
      3) recorta truth
    """
    # 1) Caps por bloque (chars)
    truth = _clip_text("truth", truth_text or "", MAX_TRUTH_CHARS)
    context = _clip_text("context", context_text or "", MAX_CONTEXT_CHARS)
    confluence = _clip_text("confluence", confluence_text or "", MAX_CONFLUENCE_CHARS)

    # 2) Presupuesto REAL: modelo - system_tokens - overhead
    system_tokens_est = _estimate_system_tokens_initial()
    available_user_tokens = max(0, LLM_MAX_TOKENS - system_tokens_est - SAFETY_OVERHEAD_TOKENS)
    available_user_chars = int(available_user_tokens * CHARS_PER_TOKEN)

    def total_chars(t: str, c: str, cf: str) -> int:
        # separadores + algo de overhead textual del prompt user
        return len(t) + len(c) + len(cf) + 650

    def total_tokens(t: str, c: str, cf: str) -> int:
        return _approx_tokens_from_chars(total_chars(t, c, cf))

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

    # 4) Si aún excede, hard clip con reparto (context primero)
    if tok > available_user_tokens:
        target_chars = int(available_user_chars * 0.92)  # margen adicional
        if target_chars <= 0:
            target_chars = int(LLM_MAX_TOKENS * CHARS_PER_TOKEN * 0.50)

        # Reparto cuando vamos justos: 65% truth, 35% context
        truth_budget = min(MAX_TRUTH_CHARS, int(target_chars * 0.65))
        ctx_budget = min(MAX_CONTEXT_CHARS, int(target_chars * 0.35))

        if len(truth) < truth_budget:
            extra = truth_budget - len(truth)
            ctx_budget = min(MAX_CONTEXT_CHARS, ctx_budget + extra)

        truth = _clip_text("truth_hard", truth, truth_budget)
        context = _clip_text("context_hard", context, ctx_budget)

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


def run_main(us_key: str):
    print("--- DIAGNÓSTICO DE INICIO ---", flush=True)

    if not us_key:
        print("ERROR: MANUAL_ISSUE_KEY no detectada.", flush=True)
        return

    print(f"--- Procesando: {us_key} ---", flush=True)
    us_data = get_issue(us_key)
    if not us_data:
        return

    us_summary = us_data["fields"].get("summary", "")
    us_description_raw = us_data["fields"].get("description", "") or ""
    _ = strip_html_tags(us_description_raw)

    print(f"Resumen US:\n\n{us_summary}", flush=True)

    # 1) TRUTH SOURCES
    dependency_keys = get_dependency_issue_keys(us_data)
    truth_seed_keys = [us_key] + [k for k in dependency_keys if k != us_key]
    truth_seed_keys = list(dict.fromkeys(truth_seed_keys))

    if dependency_keys:
        print(f"INFO: Detectadas dependencias (truth candidates): {dependency_keys}", flush=True)

    truth_linked_keys = []
    for seed_key in truth_seed_keys:
        seed_data = us_data if seed_key == us_key else get_issue(seed_key)
        if not seed_data:
            continue
        seed_desc_raw = (seed_data.get("fields", {}) or {}).get("description", "") or ""
        keys_in_desc, _conf = _extract_jira_keys_and_conf_urls(seed_desc_raw)
        for k in keys_in_desc:
            if k not in truth_seed_keys and k not in truth_linked_keys and k != us_key:
                truth_linked_keys.append(k)

    truth_issue_keys = list(dict.fromkeys(truth_seed_keys + truth_linked_keys))

    if len(truth_issue_keys) > MAX_TRUTH_ISSUES:
        kept = truth_issue_keys[:MAX_TRUTH_ISSUES]
        dropped = truth_issue_keys[MAX_TRUTH_ISSUES:]
        truth_issue_keys = kept
        print(
            f"WARN: TRUTH ampliado excede MAX_TRUTH_ISSUES={MAX_TRUTH_ISSUES}. "
            f"Se mantienen: {kept}. Se excluyen de TRUTH: {dropped}",
            flush=True,
        )

    truth_issues = []
    for key in truth_issue_keys:
        data = us_data if key == us_key else get_issue(key)
        if not data:
            continue
        summary = data.get("fields", {}).get("summary", "") or ""
        desc_raw = data.get("fields", {}).get("description", "") or ""
        desc = strip_html_tags(desc_raw)
        desc = _clip_text(f"truth:{key}", desc, MAX_TRUTH_BLOCK_CHARS_PER_ISSUE)

        truth_issues.append(
            {"key": key, "summary": summary, "description": desc, "description_raw": desc_raw}
        )

    # 2) ANCHOR para E2E
    epic_key = get_epic_link_key(us_data)
    parent_epic_key = us_key

    if epic_key:
        print(f"INFO: Epic Link detectado: {epic_key}", flush=True)
        epic_data = get_issue(epic_key)
        if epic_data:
            possible_parent = get_parent_epic_key(epic_data)
            parent_epic_key = possible_parent if possible_parent else epic_key
            print(f"INFO: Jerarquía vinculación E2E -> {parent_epic_key}", flush=True)

    # 3) CONTEXTO AMPLIADO
    extra_context = ""
    visited_issue_keys = set(truth_issue_keys)

    referenced_issue_keys_used = []
    confluence_urls_used = []

    def add_context_block(header: str, body: str):
        nonlocal extra_context
        if not body:
            return
        extra_context += f"\n{header}:\n{body}\n"

    conf_urls_all = []
    referenced_keys_seed = []

    for t in truth_issues:
        keys, conf_urls = _extract_jira_keys_and_conf_urls(t["description_raw"] or "")
        referenced_keys_seed.extend([k for k in keys if k not in truth_issue_keys])
        conf_urls_all.extend(conf_urls)

    conf_urls_all = list(dict.fromkeys(conf_urls_all))
    referenced_keys_seed = list(dict.fromkeys(referenced_keys_seed))

    if referenced_keys_seed:
        print(f"INFO: Detectadas {len(referenced_keys_seed)} referencias Jira en TRUTH (descripciones).", flush=True)

    if conf_urls_all:
        print(f"INFO: Detectados {len(conf_urls_all)} enlaces de Confluence en TRUTH.", flush=True)
        for url in conf_urls_all:
            content = get_confluence_content(url) or ""
            if content:
                confluence_urls_used.append(url)
                add_context_block(f"DOCUMENTO CONFLUENCE {url}", _clip_text("confluence", content, 1200))

    queue = [(k, 1) for k in referenced_keys_seed]
    extracted_count = 0

    while queue and extracted_count < MAX_REFERENCED_JIRA_TICKETS:
        key, depth = queue.pop(0)
        if key in visited_issue_keys:
            continue

        issue_data = get_issue(key)
        if not issue_data:
            visited_issue_keys.add(key)
            continue

        summary = issue_data.get("fields", {}).get("summary", "") or ""
        desc_raw = issue_data.get("fields", {}).get("description", "") or ""
        desc = strip_html_tags(desc_raw) or ""
        desc = _clip_text(f"ref:{key}", desc, MAX_REFERENCED_JIRA_DESC_CHARS)

        print(f"INFO: Contexto extraído de ticket vinculado (ref): {key}", flush=True)
        referenced_issue_keys_used.append(key)
        add_context_block(f"INFO TICKET REFERENCIADO {key} | {summary}", desc)

        visited_issue_keys.add(key)
        extracted_count += 1

        if depth > 0 and CONTEXT_REFERENCE_DEPTH > 0:
            keys2, conf2 = _extract_jira_keys_and_conf_urls(desc_raw)
            for u in conf2:
                if u not in conf_urls_all:
                    conf_urls_all.append(u)
            for k2 in keys2:
                if k2 not in visited_issue_keys and k2 not in truth_issue_keys:
                    queue.append((k2, depth - 1))

    # Documentación de épica/anchor (CONTEXTO)
    contexto_epica = ""
    parent_data = get_issue(parent_epic_key) if parent_epic_key else None
    doc_url = get_doc_link(parent_data) if parent_data else None
    if doc_url:
        print(f"INFO: Consultando documentación de la Épica/Anchor: {doc_url}", flush=True)
        contexto_epica = get_confluence_content(doc_url) or ""
        if contexto_epica:
            confluence_urls_used.append(doc_url)
        contexto_epica = _clip_text("epic_confluence", contexto_epica, MAX_EPIC_CONFLUENCE_CHARS)

    context_provenance = {
        "truth_issues": truth_issue_keys,
        "referenced_issues": list(dict.fromkeys(referenced_issue_keys_used)),
        "confluence_urls": list(dict.fromkeys(confluence_urls_used)),
        "anchor_epic": parent_epic_key,
    }

    print("\n" + "=" * 50, flush=True)
    print("TRAZABILIDAD GLOBAL DE CONTEXTO (TRUTH + CONTEXTO ADICIONAL):", flush=True)
    print(f"- Truth issues (seed+linked): {context_provenance['truth_issues']}", flush=True)
    print(f"- Referenced issues usados (context): {context_provenance['referenced_issues']}", flush=True)
    print(f"- Confluence usado (context): {context_provenance['confluence_urls']}", flush=True)
    print(f"- Anchor E2E: {context_provenance['anchor_epic']}", flush=True)
    print("=" * 50 + "\n", flush=True)

    # 4) TRUTH TEXT
    truth_blocks = []
    for t in truth_issues:
        truth_blocks.append(
            f"Ticket: {t['key']}\n"
            f"Resumen: {t['summary']}\n"
            f"Descripción:\n{t['description']}\n"
        )
    truth_text = "\n\n---\n\n".join(truth_blocks)

    # 5) Payload LLM con control REAL
    payload = build_llm_payload(
        truth_text=truth_text,
        context_text=extra_context,
        confluence_text=contexto_epica,
    )

    if LOG_CONTEXT_DECISIONS:
        print("\n" + "=" * 60, flush=True)
        print("LLM CONTEXT DEBUG (ANTES DE LLAMAR A IA)", flush=True)
        print(f"- system_tokens_est: {payload.get('system_tokens_est')} (incluye contrato inicial)", flush=True)
        print(f"- available_user_tokens: {payload.get('available_user_tokens')}", flush=True)
        print(f"- user_payload_tokens_est: {payload.get('approx_tokens_user_payload')}", flush=True)
        print(f"- dropped_confluence: {payload.get('dropped_confluence')}", flush=True)
        print("=" * 60 + "\n", flush=True)

    # 6) Prompt inicial (usa payload recortado)
    base_user_prompt = f"""
### MANDATO DE GENERACIÓN MASIVA (100% COBERTURA)
Analiza cada frase de la FUENTE DE VERDAD y su contexto técnico vinculado.
1. Elabora un inventario técnico completo de cada parámetro, flujo y configuración encontrada.
2. Genera escenarios específicos. NO TE SALTES NINGÚN PUNTO DEL INVENTARIO.
3. Debe existir correspondencia exacta 1:1 entre inventario y escenarios.

### PISTA PARA SCOPE (SYSTEM vs E2E) - OBLIGATORIO
- Si el punto implica UI/UX observable (foco, navegación, animaciones, preview, botones, carrusel), marca scope="E2E".
- Si el punto es puramente de datos/backend (orden, filtros, exclusiones), marca scope="System".
- Intenta que ~30-40% de escenarios sean E2E cuando el feature sea principalmente UI.

### AUTOMATIZACIÓN (OBLIGATORIO EN JSON)
- Para cada escenario, rellena SIEMPRE: automation_candidate, automation_type, automation_code.
- Si automation_candidate=true:
  - automation_type NO puede ser none
  - PROHIBIDO placeholders tipo 'selenium_code_for_*', 'TODO', '...'
  - automation_code >= 600 caracteres con imports + setup + navegación + locators + asserts + teardown

### FUENTE DE VERDAD (TRUTH SOURCES)
{payload['truth']}

### CONTEXTO ADICIONAL (APOYO)
{payload['context']}
"""

    if payload.get("confluence"):
        base_user_prompt += f"""

### DOCUMENTACIÓN DE ÉPICA/ANCHOR (APOYO)
{payload['confluence']}
"""

    base_user_prompt += """
### TAREA
Devuelve:
- Inventario Técnico (1..N) + TOTAL_INVENTARIO: N
- JSON_START + Array JSON + JSON_END
- Todo en español
- formatted_description SIN TABLAS y conciso
"""

    respuesta = ask_inventory_and_initial_scenarios(base_user_prompt)
    if not respuesta:
        return
    dump_raw_response(respuesta, us_key, suffix="initial")

    analisis, scenarios = extract_analysis_and_json(respuesta)
    inventory_text = extract_inventory_block(respuesta)
    n_total = extract_total_inventory(respuesta)

    print("\n" + "=" * 50, flush=True)
    print("INVENTARIO TÉCNICO CONSIDERADO POR LA IA:", flush=True)
    print(analisis if analisis else inventory_text, flush=True)
    print("=" * 50 + "\n", flush=True)

    if n_total is None:
        print("ERROR: No se detectó TOTAL_INVENTARIO en la respuesta inicial. Abortando.", flush=True)
        return

    if not isinstance(scenarios, list):
        scenarios = []

    # 8) Compact context para missing
    compact_completion_context = (
        "FUENTE DE VERDAD (TRUTH SOURCES):\n"
        f"{payload['truth']}\n\n"
        "CONTEXTO ADICIONAL (APOYO):\n"
        f"{payload['context']}\n"
    )
    if payload.get("confluence"):
        compact_completion_context += (
            "\nDOCUMENTACIÓN DE ÉPICA/ANCHOR (APOYO):\n"
            f"{payload['confluence']}\n"
        )
    if len(compact_completion_context) > MAX_COMPLETION_CONTEXT_CHARS:
        compact_completion_context = compact_completion_context[:MAX_COMPLETION_CONTEXT_CHARS]

    # 9) Completado missing
    MAX_COMPLETION_ATTEMPTS = 6
    BATCH_SIZE = 5

    attempt = 0
    while attempt < MAX_COMPLETION_ATTEMPTS:
        attempt += 1
        missing = missing_inventory_ids(n_total, scenarios)
        if not missing:
            break

        print(
            f"INFO: Faltan {len(missing)} escenarios para completar cobertura 1..{n_total}. "
            f"Intento {attempt}/{MAX_COMPLETION_ATTEMPTS}",
            flush=True,
        )

        batch = missing[:BATCH_SIZE]
        resp_missing = ask_missing_scenarios(inventory_text, compact_completion_context, batch)
        if not resp_missing:
            print("WARN: No hubo respuesta en completado. Reintentando...", flush=True)
            continue

        dump_raw_response(resp_missing, us_key, suffix=f"missing_attempt_{attempt}")

        _, new_scenarios = extract_analysis_and_json(resp_missing)
        if not new_scenarios:
            print("WARN: No se pudieron parsear escenarios faltantes en este batch. Reintentando...", flush=True)
            continue

        scenarios = normalize_scenarios_merge(scenarios, new_scenarios)

    missing_final = missing_inventory_ids(n_total, scenarios)
    if missing_final:
        print(f"WARN: Tras reintentos, aún faltan IDs: {missing_final}. Ejecutando último intento...", flush=True)
        resp_last = ask_missing_scenarios(inventory_text, compact_completion_context, missing_final)
        if resp_last:
            dump_raw_response(resp_last, us_key, suffix="missing_last")
            _, new_last = extract_analysis_and_json(resp_last)
            if new_last:
                scenarios = normalize_scenarios_merge(scenarios, new_last)

    raw_text_for_validation = inventory_text
    if "TOTAL_INVENTARIO:" not in raw_text_for_validation:
        raw_text_for_validation += f"\nTOTAL_INVENTARIO: {n_total}\n"

    ok, msg = validate_scenarios_coverage(raw_text_for_validation, scenarios)
    if not ok:
        print(f"ERROR: Validación de cobertura fallida: {msg}", flush=True)
        print(f"INFO: Recibidos escenarios={len(scenarios)} de TOTAL_INVENTARIO={n_total}.", flush=True)
        print("INFO: No se crearán Test Cases en Jira para evitar cobertura parcial.", flush=True)
        return

    # 10) Crear TCs
    print(f"INFO: Cobertura OK. Procesando {len(scenarios)} escenarios generados...", flush=True)
    scenarios_sorted = sorted(scenarios, key=lambda x: int(x.get("inventory_id", 0)))

    for sc in scenarios_sorted:
        title = sc.get("test_title", "Test")
        scope = sc.get("scope", "System")
        is_e2e = scope.lower() in ["e2e", "end2end"]

        link_target_primary = parent_epic_key if is_e2e else us_key

        summary_base = f"[{sc.get('main_function', 'QA')}] {title}"
        print(f"--- Creando: {summary_base} ({scope}) ---", flush=True)

        print(
            f"DEBUG AUTO inventory_id={sc.get('inventory_id')} "
            f"cand={sc.get('automation_candidate')} type={sc.get('automation_type')} "
            f"code_len={len((sc.get('automation_code') or '').strip())}",
            flush=True,
        )

        automation_candidate_value = compute_automation_label(sc)

        manual_desc = normalize_jira_wiki(sc.get("formatted_description", "") or "")

        # KPI desactivado (acordado)
        # manual_desc = normalize_jira_wiki(append_kpi_block_option_a(manual_desc, sc))

        final_manual = to_corporate_template(manual_desc)
        final_desc = append_automation_block_to_description(final_manual, sc)

        log_scenario_sources(sc, context_provenance)

        tc_key = create_test_case(
            TARGET_PROJECT,
            f"{summary_base} - Manual",
            final_desc,
            link_target_primary,
            scope,
            "Manual",
            automation_candidate_value=automation_candidate_value,
        )

        if tc_key and is_e2e:
            if us_key and link_target_primary != us_key:
                link_issues(us_key, tc_key)

    print(f"--- Proceso finalizado para {us_key} ---", flush=True)
