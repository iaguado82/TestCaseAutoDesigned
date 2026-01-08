# scenario_engine.py
from typing import Dict, Any, List, Tuple

from .github_models_client import call_github_models
from .prompts import (
    system_contract_no_tables_inventory_and_json,
    system_contract_only_missing_json_no_tables,
)
from .utils_text import dump_raw_response
from .utils_ai_parse import (
    extract_analysis_and_json,
    extract_inventory_block,
    extract_total_inventory,
    missing_inventory_ids,
    normalize_scenarios_merge,
    validate_scenarios_coverage,
)


MAX_COMPLETION_CONTEXT_CHARS = 3500
MAX_COMPLETION_ATTEMPTS = 6
BATCH_SIZE = 5


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


def build_base_user_prompt(payload: Dict[str, Any]) -> str:
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
    return base_user_prompt


def build_compact_completion_context(payload: Dict[str, Any]) -> str:
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
    return compact_completion_context


def generate_scenarios_with_full_coverage(
    us_key: str,
    payload: Dict[str, Any],
    log_context_decisions: bool = False,
) -> Tuple[bool, str, Dict[str, Any]]:
    """
    Orquesta:
    - llamada inicial (inventario + escenarios)
    - reintentos de missing
    - validación de cobertura

    Retorna:
    - ok (bool)
    - message (str)
    - result dict: {inventory_text, n_total, scenarios, analysis_text}
    """
    base_user_prompt = build_base_user_prompt(payload)

    respuesta = ask_inventory_and_initial_scenarios(base_user_prompt)
    if not respuesta:
        return False, "No se obtuvo respuesta de IA en llamada inicial.", {}

    dump_raw_response(respuesta, us_key, suffix="initial")

    analysis_text, scenarios = extract_analysis_and_json(respuesta)
    inventory_text = extract_inventory_block(respuesta)
    n_total = extract_total_inventory(respuesta)

    if n_total is None:
        return False, "No se detectó TOTAL_INVENTARIO en la respuesta inicial.", {}

    if not isinstance(scenarios, list):
        scenarios = []

    compact_ctx = build_compact_completion_context(payload)

    attempt = 0
    while attempt < MAX_COMPLETION_ATTEMPTS:
        attempt += 1
        missing = missing_inventory_ids(n_total, scenarios)
        if not missing:
            break

        batch = missing[:BATCH_SIZE]

        resp_missing = ask_missing_scenarios(inventory_text, compact_ctx, batch)
        if not resp_missing:
            continue

        dump_raw_response(resp_missing, us_key, suffix=f"missing_attempt_{attempt}")

        _, new_scenarios = extract_analysis_and_json(resp_missing)
        if not new_scenarios:
            continue

        scenarios = normalize_scenarios_merge(scenarios, new_scenarios)

    missing_final = missing_inventory_ids(n_total, scenarios)
    if missing_final:
        resp_last = ask_missing_scenarios(inventory_text, compact_ctx, missing_final)
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
        return False, f"Validación de cobertura fallida: {msg}", {
            "inventory_text": inventory_text,
            "n_total": n_total,
            "scenarios": scenarios,
            "analysis_text": analysis_text,
        }

    return True, "Cobertura OK", {
        "inventory_text": inventory_text,
        "n_total": n_total,
        "scenarios": scenarios,
        "analysis_text": analysis_text,
    }
