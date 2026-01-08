import re

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
    append_kpi_block_option_a,
)
from .jira_client import (
    get_issue,
    get_parent_epic_key,
    create_test_case,
    get_doc_link,
    get_epic_link_key,
    get_dependency_issue_keys,
    link_issues,  # <-- NUEVO: para vincular también a la US en E2E
)
from .confluence_client import get_confluence_content
from .github_models_client import call_github_models
from .prompts import (
    system_contract_no_tables_inventory_and_json,
    system_contract_only_missing_json_no_tables,
)

# ---------------------------
# Presupuestos / límites
# ---------------------------
MAX_FULL_CONTEXT_CHARS = 7000
MAX_TRUTH_BLOCK_CHARS_PER_ISSUE = 8000
MAX_REFERENCED_JIRA_TICKETS = 8
MAX_REFERENCED_JIRA_DESC_CHARS = 900
MAX_EPIC_CONFLUENCE_CHARS = 2500
MAX_COMPLETION_CONTEXT_CHARS = 3500
CONTEXT_REFERENCE_DEPTH = 1

# ---------------------------
# Utilidades parsing / normalización
# ---------------------------
JIRA_KEY_RE = re.compile(r'([A-Z][A-Z0-9]+-\d+)')
CONF_URL_RE = re.compile(r'https?://confluence\.tid\.es/[^\s\]\)\|\,\>\"\' ]+')


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


def ask_inventory_and_initial_scenarios(prompt):
    system_content = system_contract_no_tables_inventory_and_json()
    messages = [
        {"role": "system", "content": system_content},
        {"role": "user", "content": prompt}
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
        {"role": "user", "content": user_prompt}
    ]
    return call_github_models(messages, temperature=0.2, timeout=180)


def _extract_jira_keys_and_conf_urls(text: str):
    if not text:
        return [], []
    keys = list(dict.fromkeys(JIRA_KEY_RE.findall(text)))
    conf_urls = list(dict.fromkeys(CONF_URL_RE.findall(text)))
    return keys, conf_urls


def _clip_text(label: str, text: str, max_chars: int) -> str:
    if not text:
        return ""
    if len(text) <= max_chars:
        return text
    head = text[: int(max_chars * 0.7)]
    tail = text[-int(max_chars * 0.25):]
    return f"{head}\n\n[... RECORTADO ({label}) ...]\n\n{tail}"


def run_main(us_key: str):
    print("--- DIAGNÓSTICO DE INICIO ---", flush=True)

    if not us_key:
        print("ERROR: MANUAL_ISSUE_KEY no detectada.", flush=True)
        return

    print(f"--- Procesando: {us_key} ---", flush=True)
    us_data = get_issue(us_key)
    if not us_data:
        return

    us_summary = us_data['fields'].get('summary', '')
    us_description_raw = us_data['fields'].get('description', '') or ""
    us_description = strip_html_tags(us_description_raw)

    print(f"Resumen US: {us_summary}", flush=True)

    # 1) TRUTH SOURCES: US + dependencies (is a dependency for)
    dependency_keys = get_dependency_issue_keys(us_data)
    truth_issue_keys = [us_key] + [k for k in dependency_keys if k != us_key]
    truth_issue_keys = list(dict.fromkeys(truth_issue_keys))

    if dependency_keys:
        print(f"INFO: Detectadas dependencias (truth candidates): {dependency_keys}", flush=True)

    truth_issues = []
    for key in truth_issue_keys:
        data = us_data if key == us_key else get_issue(key)
        if not data:
            continue
        summary = data.get("fields", {}).get("summary", "") or ""
        desc_raw = data.get("fields", {}).get("description", "") or ""
        desc = strip_html_tags(desc_raw)
        desc = _clip_text(f"truth:{key}", desc, MAX_TRUTH_BLOCK_CHARS_PER_ISSUE)

        truth_issues.append({
            "key": key,
            "summary": summary,
            "description": desc,
            "description_raw": desc_raw,
        })

    # 2) ANCHOR para E2E: epic chain
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
    budget_left = MAX_FULL_CONTEXT_CHARS
    extra_context = ""
    visited_issue_keys = set(truth_issue_keys)

    def add_context_block(header: str, body: str):
        nonlocal extra_context, budget_left
        if not body or budget_left <= 0:
            return
        chunk = f"\n{header}:\n{body}\n"
        if len(chunk) > budget_left:
            chunk = chunk[:budget_left]
        extra_context += chunk
        budget_left -= len(chunk)

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
            if budget_left <= 0:
                break
            content = get_confluence_content(url) or ""
            if content:
                add_context_block(f"DOCUMENTO CONFLUENCE {url}", _clip_text("confluence", content, 1200))

    queue = [(k, 1) for k in referenced_keys_seed]
    extracted_count = 0

    while queue and extracted_count < MAX_REFERENCED_JIRA_TICKETS and budget_left > 0:
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

    contexto_epica = ""
    parent_data = get_issue(parent_epic_key) if parent_epic_key else None
    doc_url = get_doc_link(parent_data) if parent_data else None
    if doc_url:
        print(f"INFO: Consultando documentación de la Épica/Anchor: {doc_url}", flush=True)
        contexto_epica = get_confluence_content(doc_url) or ""
        contexto_epica = _clip_text("epic_confluence", contexto_epica, MAX_EPIC_CONFLUENCE_CHARS)

    full_context = f"{extra_context}\n{contexto_epica}".strip()

    # 4) Prompt inicial
    truth_blocks = []
    for t in truth_issues:
        truth_blocks.append(
            f"Ticket: {t['key']}\n"
            f"Resumen: {t['summary']}\n"
            f"Descripción:\n{t['description']}\n"
        )
    truth_text = "\n\n---\n\n".join(truth_blocks)

    compact_completion_context = (
        "FUENTE DE VERDAD (TRUTH SOURCES):\n"
        f"{_clip_text('truth_compact', truth_text, 2200)}\n\n"
        "CONTEXTO ADICIONAL (RESUMIDO):\n"
        f"{_clip_text('ctx_compact', full_context, 1200)}\n"
    )
    if len(compact_completion_context) > MAX_COMPLETION_CONTEXT_CHARS:
        compact_completion_context = compact_completion_context[:MAX_COMPLETION_CONTEXT_CHARS]

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

### KPI / RENDIMIENTO (OPCIÓN A) - OBLIGATORIO CUANDO APLIQUE
- Cuando el escenario implique transiciones, carga de pantallas, render/preview, animaciones o navegación,
  añade al final de formatted_description:
  h1. KPI / Rendimiento (si aplica)
  ----
  * Define 1-3 KPIs con Start/End (evento), método (prioridad logs/telemetría; alternativa driver),
    repeticiones (>=5) y criterio (baseline o umbral).
- No inventes herramientas internas; si faltan logs, sugiere 'instrumentar eventos *_start/*_ready'.

### AUTOMATIZACIÓN (OBLIGATORIO EN JSON)
- Para cada escenario, rellena SIEMPRE: automation_candidate, automation_type, automation_code.
- Si automation_candidate=true:
  - automation_type NO puede ser none
  - PROHIBIDO placeholders tipo 'selenium_code_for_*', 'TODO', '...'
  - automation_code >= 600 caracteres con imports + setup + navegación + locators + asserts + teardown

### FUENTE DE VERDAD (TRUTH SOURCES)
{truth_text}

### CONTEXTO ADICIONAL (APOYO: issues referenciados + confluence + épica/anchor)
{full_context}

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

    # Completado missing
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
            flush=True
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

    # Crear TCs
    print(f"INFO: Cobertura OK. Procesando {len(scenarios)} escenarios generados...", flush=True)

    scenarios_sorted = sorted(scenarios, key=lambda x: int(x.get("inventory_id", 0)))

    for sc in scenarios_sorted:
        title = sc.get('test_title', 'Test')
        scope = sc.get('scope', 'System')
        is_e2e = scope.lower() in ["e2e", "end2end"]

        # Link primario:
        # - E2E: anchor/JEFE
        # - System: US
        link_target_primary = parent_epic_key if is_e2e else us_key

        summary_base = f"[{sc.get('main_function', 'QA')}] {title}"
        print(f"--- Creando: {summary_base} ({scope}) ---", flush=True)

        print(
            f"DEBUG AUTO inventory_id={sc.get('inventory_id')} "
            f"cand={sc.get('automation_candidate')} type={sc.get('automation_type')} "
            f"code_len={len((sc.get('automation_code') or '').strip())}",
            flush=True
        )

        automation_candidate_value = compute_automation_label(sc)

        manual_desc = normalize_jira_wiki(sc.get('formatted_description', '') or "")
        desc_with_kpi = normalize_jira_wiki(append_kpi_block_option_a(manual_desc, sc))
        final_desc = append_automation_block_to_description(desc_with_kpi, sc)

        tc_key = create_test_case(
            TARGET_PROJECT,
            f"{summary_base} - Manual",
            final_desc,
            link_target_primary,
            scope,
            "Manual",
            automation_candidate_value=automation_candidate_value
        )

        # NUEVO: Si es E2E, además de linkar a JEFE/anchor, también linkar a la US
        if tc_key and is_e2e:
            # Evita duplicar si por configuración el anchor fuese la propia US
            if us_key and link_target_primary != us_key:
                link_issues(us_key, tc_key)

    print(f"--- Proceso finalizado para {us_key} ---", flush=True)
