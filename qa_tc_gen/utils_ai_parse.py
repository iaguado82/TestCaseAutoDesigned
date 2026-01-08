import json
import re


def extract_total_inventory(text):
    """
    Extrae N de la línea 'TOTAL_INVENTARIO: N' si existe.
    """
    m = re.search(r"TOTAL_INVENTARIO:\s*(\d+)", text)
    if not m:
        return None
    try:
        return int(m.group(1))
    except Exception:
        return None


def extract_inventory_block(text):
    """
    Extrae el bloque del inventario (todo lo anterior a JSON_START si existe),
    o todo lo anterior al primer '[' como fallback.
    """
    marker_start = "JSON_START"
    if marker_start in text:
        before, _ = text.split(marker_start, 1)
        return before.strip()

    start_index = text.find('[')
    if start_index != -1:
        return text[:start_index].strip()

    return text.strip()


def extract_analysis_and_json(text):
    """
    Separa el análisis técnico previo del bloque JSON.

    Método robusto:
    - Busca JSON entre marcadores JSON_START y JSON_END.

    Fallback:
    - Si no hay marcadores, usa el método antiguo con corchetes.
    """
    analysis = ""
    scenarios = []

    marker_start = "JSON_START"
    marker_end = "JSON_END"

    if marker_start in text and marker_end in text:
        try:
            before, after_start = text.split(marker_start, 1)
            json_part, _ = after_start.split(marker_end, 1)

            analysis = before.strip()
            analysis = re.sub(r'```json|```', '', analysis).strip()

            json_str = json_part.strip()
            scenarios = json.loads(json_str)
            return analysis, scenarios
        except Exception as e:
            print(f"DEBUG: Error parseando JSON entre marcadores: {e}", flush=True)
            return analysis, []

    # Fallback antiguo
    start_index = text.find('[')
    end_index = text.rfind(']')

    if start_index != -1 and end_index != -1 and end_index > start_index:
        analysis = text[:start_index].strip()
        analysis = re.sub(r'```json|```', '', analysis).strip()

        json_str = text[start_index:end_index + 1]
        try:
            scenarios = json.loads(json_str)
        except Exception as e:
            print(f"DEBUG: Error parseando JSON dentro del bloque (fallback): {e}", flush=True)

    return analysis, scenarios


def validate_scenarios_coverage(raw_text, scenarios):
    """
    Valida coherencia:
    - TOTAL_INVENTARIO: N existe y es int.
    - len(scenarios) == N
    - inventory_id existe en todos, es int, y cubre exactamente 1..N sin duplicados.
    - Campos mínimos obligatorios: main_function, test_title, scope, formatted_description.
    - Campos de automatización esperados (se piden siempre):
      automation_candidate (bool), automation_type (str), automation_code (str)
    """
    n = extract_total_inventory(raw_text)
    if n is None:
        return False, "No se encontró TOTAL_INVENTARIO: N en la respuesta."

    if not isinstance(scenarios, list):
        return False, "El JSON no es un array."

    if len(scenarios) != n:
        return False, f"Mismatch: TOTAL_INVENTARIO={n} pero escenarios={len(scenarios)}."

    ids = []
    for i, sc in enumerate(scenarios):
        if not isinstance(sc, dict):
            return False, f"Escenario en posición {i} no es un objeto."
        if "inventory_id" not in sc:
            return False, f"Falta inventory_id en escenario en posición {i}."
        try:
            ids.append(int(sc["inventory_id"]))
        except Exception:
            return False, f"inventory_id no es int en escenario en posición {i}."

        for req in ["main_function", "test_title", "scope", "formatted_description"]:
            if req not in sc:
                return False, f"Falta '{req}' en escenario con inventory_id={sc.get('inventory_id')}."

        for req in ["automation_candidate", "automation_type", "automation_code"]:
            if req not in sc:
                return False, f"Falta '{req}' en escenario con inventory_id={sc.get('inventory_id')}."

        if not isinstance(sc.get("automation_candidate"), bool):
            return False, f"automation_candidate no es boolean en inventory_id={sc.get('inventory_id')}."
        if not isinstance(sc.get("automation_type"), str):
            return False, f"automation_type no es string en inventory_id={sc.get('inventory_id')}."
        if not isinstance(sc.get("automation_code"), str):
            return False, f"automation_code no es string en inventory_id={sc.get('inventory_id')}."

    ids_sorted = sorted(ids)
    expected = list(range(1, n + 1))
    if ids_sorted != expected:
        return False, f"inventory_id no cubre 1..{n} exactamente. Recibidos: {ids_sorted}"

    return True, "OK"


def normalize_scenarios_merge(existing, new_items):
    """
    Merge por inventory_id.
    - Mantiene el existente, PERO si el nuevo trae automation_code con contenido y el existente no,
      actualiza los campos de automatización.
    """
    by_id = {}

    def to_int(v):
        try:
            return int(v)
        except Exception:
            return None

    for sc in existing:
        iid = to_int(sc.get("inventory_id"))
        if iid is None:
            continue
        if iid not in by_id:
            by_id[iid] = sc

    for sc in new_items:
        iid = to_int(sc.get("inventory_id"))
        if iid is None:
            continue

        if iid not in by_id:
            by_id[iid] = sc
            continue

        old = by_id[iid]
        old_code = (old.get("automation_code") or "").strip()
        new_code = (sc.get("automation_code") or "").strip()

        if (not old_code) and new_code:
            old["automation_candidate"] = sc.get("automation_candidate", old.get("automation_candidate", False))
            old["automation_type"] = sc.get("automation_type", old.get("automation_type", "none"))
            old["automation_code"] = sc.get("automation_code", old.get("automation_code", ""))

    merged = [by_id[i] for i in sorted(by_id.keys())]
    return merged


def missing_inventory_ids(total_n, scenarios):
    present = set()
    for sc in scenarios:
        try:
            present.add(int(sc.get("inventory_id")))
        except Exception:
            pass
    return [i for i in range(1, total_n + 1) if i not in present]
