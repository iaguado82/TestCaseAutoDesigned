import os
import requests
import json
import sys
import re
import time

# Forzar salida más fiable en consola (evita que se “pierdan” prints puntuales)
try:
    sys.stdout.reconfigure(line_buffering=True)
except Exception:
    pass

# Soporte para carga de variables de entorno locales
try:
    from dotenv import load_dotenv
    env_loaded = load_dotenv()
except ImportError:
    env_loaded = False


def clean_token(token_str):
    """Limpia el token para evitar errores de formato 400."""
    if not token_str:
        return ""
    t = token_str.strip()
    t = t.replace('"', '').replace("'", "")
    if t.lower().startswith("bearer "):
        t = t[7:].strip()
    t = "".join(char for char in t if 32 < ord(char) < 127)
    return t


def strip_html_tags(text):
    """Elimina etiquetas HTML/XML para reducir el conteo de tokens sin perder el texto."""
    if not text:
        return ""
    clean = re.compile('<.*?>')
    text = re.sub(clean, ' ', text)
    text = re.sub(r'\s+', ' ', text).strip()
    return text


def dump_raw_response(text, us_key, suffix=""):
    """
    Guarda la respuesta cruda de la IA en un fichero para depuración.
    """
    ts = time.strftime("%Y%m%d_%H%M%S")
    suffix_part = f"_{suffix}" if suffix else ""
    filename = f"debug_raw_ai_response_{us_key}{suffix_part}_{ts}.txt"
    try:
        with open(filename, "w", encoding="utf-8") as f:
            f.write(text)
        print(f"DEBUG: Respuesta cruda de la IA guardada en: {filename}", flush=True)
    except Exception as e:
        print(f"DEBUG ERROR: No se pudo guardar la respuesta cruda: {e}", flush=True)


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


def is_true(v):
    """Normaliza truthy para posibles variantes del modelo."""
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return v != 0
    if isinstance(v, str):
        return v.strip().lower() in ["true", "yes", "1", "si", "sí"]
    return False


def looks_like_placeholder(code: str) -> bool:
    c = (code or "").strip().lower()
    if not c:
        return True
    bad_tokens = [
        "todo", "...", "placeholder", "selenium_code_for_", "appium_code_for_",
        "lorem", "tbd", "por completar"
    ]
    return any(t in c for t in bad_tokens)


def looks_like_fake_endpoint_or_auth(code: str) -> bool:
    """
    Heurística: descarta snippets con dominios de ejemplo o auth fake.
    """
    c = (code or "").lower()
    bad = [
        "example.com", "http://example", "https://example",
        "bearer token", "your_token", "insert_token", "changeme",
        "mib.example", "api.example", "testapplication.com"
    ]
    return any(b in c for b in bad)


def is_mutating_api_code(code: str) -> bool:
    """
    Heurística: descarta automatizaciones API que mutan estado (MiB/config/backoffice)
    salvo que tengáis harness real (no lo asumimos).
    """
    low = (code or "").lower()
    mutating_markers = [
        "requests.post", "requests.put", "requests.patch", "requests.delete",
        ".post(", ".put(", ".patch(", ".delete("
    ]
    return any(m in low for m in mutating_markers)


def mentions_backoffice_or_config(sc) -> bool:
    """
    Heurística textual para detectar pruebas de configuración/backoffice.
    """
    t = (sc.get("test_title") or "").lower()
    d = (sc.get("formatted_description") or "").lower()
    k = " ".join([t, d])
    tokens = [
        "mib", "backoffice", "cms", "configur", "configuración", "parametr",
        "feature flag", "toggle", "habilitar", "deshabilitar"
    ]
    return any(tok in k for tok in tokens)


def is_quality_automation(auto_type: str, code: str, sc=None) -> bool:
    """
    Quality gate mínimo para evitar marcar High/Low con snippets/placeholder.
    Si no pasa, se marca Discarded.
    """
    c = (code or "").strip()
    t = (auto_type or "none").strip().lower()
    sc = sc or {}

    if t not in ["selenium", "appium", "api"]:
        return False
    if looks_like_placeholder(c):
        return False
    if looks_like_fake_endpoint_or_auth(c):
        return False

    # Nueva regla: si huele a backoffice/config, por defecto Discarded
    if mentions_backoffice_or_config(sc):
        return False

    # API: no permitimos mutaciones (POST/PUT/PATCH/DELETE) como candidate por defecto
    if t == "api" and is_mutating_api_code(c):
        return False

    # Umbral mínimo de contenido (evita 1-liners)
    if len(c) < 600:
        return False

    low = c.lower()

    if t in ["selenium", "appium"]:
        # Debe tener esperas y asserts/checks
        required_any = ["webdriverwait", "expected_conditions", "wait.until"]
        if not any(r in low for r in required_any):
            return False
        if "assert" not in low and "expect" not in low:
            return False

        # Si el test exige equivalencia ("igual", "misma"), exige assert comparativo
        td = (sc.get("formatted_description") or "").lower()
        if ("misma" in td or "igual" in td) and "==" not in low:
            return False

    if t == "api":
        if "assert" not in low and "expect" not in low:
            return False

    return True


def compute_automation_label(sc) -> str:
    """
    Mapea a valores del customfield Automation Candidate:
    - High / Low / Discarded

    Política (más conservadora, para evitar “candidatas complejas”):
    - Discarded si automation_candidate=false o no pasa quality gate.
    - High si selenium/appium y pasa quality gate y NO es config/backoffice.
    - Low  si api (solo lectura) y pasa quality gate.
    """
    candidate = is_true(sc.get("automation_candidate", False))
    auto_type = (sc.get("automation_type", "") or "none").strip().lower()
    code = sc.get("automation_code", "") or ""

    if not candidate:
        return "Discarded"
    if not is_quality_automation(auto_type, code, sc=sc):
        return "Discarded"

    if auto_type in ["selenium", "appium"]:
        return "High"
    if auto_type == "api":
        return "Low"
    return "Discarded"


def append_automation_block_to_description(manual_desc: str, sc) -> str:
    """
    Añade al final del description manual un bloque con la propuesta de automatización,
    manteniendo lo manual siempre.
    Solo añade bloque si el Automation Candidate no es Discarded.
    """
    base = manual_desc or ""
    label = compute_automation_label(sc)
    if label == "Discarded":
        return base

    auto_type = (sc.get("automation_type", "") or "none").strip().lower()
    code = (sc.get("automation_code", "") or "").strip()

    if not code:
        return base

    block = (
        "\n\n"
        "h1. Automatización (Propuesta)\n"
        "----\n"
        f"* Automation Candidate: {label}\n"
        f"* Tipo recomendado: {auto_type}\n"
        "* Nota: Este bloque es informativo. El caso de prueba manual sigue siendo la referencia ejecutable.\n\n"
        "{code:python}\n"
        f"{code}\n"
        "{code}\n"
    )
    return base + block


def append_kpi_block_option_a(manual_desc: str, sc) -> str:
    """
    Opción A: añadir KPIs en la descripción (sin cambiar el JSON).
    Añade bloque SOLO si el escenario trata de transición/carga/animación/tiempos,
    o si es E2E y menciona UI/UX (carrusel, navegación, preview, detalle, animaciones).
    """
    base = manual_desc or ""
    scope = (sc.get("scope") or "").strip().lower()
    title = (sc.get("test_title") or "").lower()
    mf = (sc.get("main_function") or "").lower()
    body = (sc.get("formatted_description") or "").lower()
    txt = " ".join([title, mf, body])

    kpi_tokens = [
        "carga", "cargar", "tiempo", "latencia", "transición", "transicion",
        "animación", "animacion", "render", "pint", "apertura", "abrir",
        "detalle", "home", "preview", "foco", "navegación", "navegacion",
        "scroll", "stutter", "frames", "jank", "progreso", "progress"
    ]
    is_uiish = any(t in txt for t in kpi_tokens)
    if not is_uiish:
        return base

    # KPI base: define start/end + método (logs primero) + umbral sugerido/baseline
    # Nota: no inventamos tags concretos; proponemos “evento A/B” y dónde instrumentar.
    block = (
        "\n\n"
        "h1. KPI / Rendimiento (si aplica)\n"
        "----\n"
        "* Objetivo: medir tiempos percibidos por usuario y detectar degradaciones.\n"
        "* Método recomendado (prioridad): Logs/telemetría de app con timestamps (evento *_start / *_ready).\n"
        "* Alternativa: medición por driver (t0 al input; t1 cuando pantalla/estado 'ready' sea observable).\n"
        "* Ejecución: 5 repeticiones mínimo; reportar p50 y p95.\n"
        "* Criterio de aceptación: si no hay SLA, usar baseline del release anterior y alertar si p95 empeora >20%.\n"
    )

    # Sugerencias concretas según tema
    # (muy pragmático: 2-3 métricas máximas)
    metrics = []
    if "detalle" in txt or "info" in txt or "opc" in txt or "ficha" in txt:
        metrics.append("* KPI: Time-to-Detail (TTD) | Start: pulsación INFO/OPC/OK | End: detalle 'ready' (contenido+UI estable).")
    if "carrusel" in txt or "home" in txt:
        metrics.append("* KPI: Time-to-Carousel-Interactive (TCI) | Start: entrada en Home | End: carrusel visible + foco navegable.")
    if "preview" in txt or "imagen" in txt or "portrait" in txt or "landscape" in txt:
        metrics.append("* KPI: Time-to-Preview (TTP) | Start: foco en item | End: preview renderizada (imagen visible).")
    if "anim" in txt or "scroll" in txt or "naveg" in txt:
        metrics.append("* KPI: Jank/Fluidez | Medida: frames drops durante navegación horizontal; si no hay métrica, registrar stutter en logs.")

    if metrics:
        block += "\n" + "\n".join(metrics) + "\n"

    return base + block


# --- 1. CONFIGURACIÓN DE ENTORNO ---
JIRA_URL = os.getenv("JIRA_URL", "https://jira.tid.es/").strip().rstrip('/')
JIRA_USERNAME = os.getenv("JIRA_USERNAME", "id02621").strip()
JIRA_PERSONAL_TOKEN = clean_token(os.getenv("JIRA_PERSONAL_TOKEN", ""))

CONFLUENCE_URL = os.getenv("CONFLUENCE_URL", "https://confluence.tid.es/").strip().rstrip('/')
CONFLUENCE_PERSONAL_TOKEN = clean_token(os.getenv("CONFLUENCE_PERSONAL_TOKEN", ""))

TARGET_PROJECT = os.getenv("TARGET_PROJECT", "MULTISTC").strip()
GITHUB_TOKEN = clean_token(os.getenv("GITHUB_TOKEN", ""))

# CONFIGURACIÓN DE PRUEBAS
# Ya NO se crean TCs "Automatic"; solo se marca el campo Automation Candidate y se añade bloque en description.
GENERATE_AUTOMATION = False

# IDs de campos personalizados TID
ID_CAMPO_EPIC_LINK = "customfield_11600"
ID_CAMPO_DOC_LINK = "customfield_22398"
ID_CAMPO_TEST_SCOPE = "customfield_10163"
ID_CAMPO_EXECUTION_MODE = "customfield_10150"

# NUEVO: Automation Candidate
ID_CAMPO_AUTOMATION_CANDIDATE = "customfield_10161"  # valores: High | Low | Discarded

jira_headers = {
    "Authorization": f"Bearer {JIRA_PERSONAL_TOKEN}",
    "Accept": "application/json",
    "Content-Type": "application/json"
}


# --- 2. FUNCIONES DE COMUNICACIÓN ---
def get_issue(issue_key):
    """Obtiene los datos de un ticket de Jira."""
    try:
        url = f"{JIRA_URL}/rest/api/2/issue/{issue_key}"
        res = requests.get(url, headers=jira_headers, timeout=30)
        if res.status_code != 200:
            print(f"Error Jira ({issue_key}): {res.status_code}", flush=True)
            return None
        return res.json()
    except Exception as e:
        print(f"Excepción grave en Jira: {e}", flush=True)
        return None


def get_parent_epic_key(epic_data):
    """Obtiene la épica padre a través del enlace 'is child of'."""
    links = epic_data['fields'].get('issuelinks', [])
    for link in links:
        l_type = link.get('type', {})
        if (link.get('inwardIssue') and l_type.get('inward') == 'is child of'):
            return link['inwardIssue']['key']
        if (link.get('outwardIssue') and l_type.get('outward') == 'is child of'):
            return link['outwardIssue']['key']
    return None


def create_test_case(project_key, summary, description, target_link_key,
                     scope="System", mode="Manual", labels=None, automation_candidate_value="Discarded"):
    """
    Crea un nuevo Test Case en Jira.
    - SIEMPRE crea el TC en modo Manual (mode=Manual), pero rellena customfield_10161 (Automation Candidate)
      con High/Low/Discarded.
    - En description se puede añadir un bloque informativo de automatización y otro de KPIs.
    """
    url = f"{JIRA_URL}/rest/api/2/issue"

    scope_value = "End2End" if scope.lower() in ["e2e", "end2end"] else "System"
    mode_value = "Automatic" if mode.lower() == "automatic" else "Manual"

    if labels is None:
        labels = []

    clean_project_key = project_key.split('-')[0]

    payload = {
        "fields": {
            "project": {"key": clean_project_key},
            "summary": summary,
            "description": description,
            "issuetype": {"name": "Test Case"},
            "labels": labels,
            ID_CAMPO_TEST_SCOPE: [{"value": scope_value}],
            ID_CAMPO_EXECUTION_MODE: {"value": mode_value},
            ID_CAMPO_AUTOMATION_CANDIDATE: {"value": automation_candidate_value}
        }
    }

    res = requests.post(url, json=payload, headers=jira_headers)
    if res.status_code == 201:
        tc_key = res.json()['key']
        print(f"Éxito: TC {tc_key} ({mode_value}/{scope_value}) creado. AutomationCandidate={automation_candidate_value}",
              flush=True)
        link_issues(target_link_key, tc_key)
        return tc_key

    print(f"Error creando TC {mode_value}: {res.status_code} - {res.text}", flush=True)
    return None


def link_issues(parent_key, tc_key):
    """Vincula tickets: TC 'tests' US / US 'is tested by' TC."""
    url = f"{JIRA_URL}/rest/api/2/issueLink"
    payload = {
        "type": {"name": "Tests"},
        "inwardIssue": {"key": parent_key},
        "outwardIssue": {"key": tc_key}
    }
    res = requests.post(url, json=payload, headers=jira_headers)
    if res.status_code == 201:
        print(f"Vínculo establecido: {tc_key} ---[tests]---> {parent_key}", flush=True)
    else:
        print(f"Error vinculando {tc_key} con {parent_key}: {res.status_code} - {res.text}", flush=True)


def get_confluence_content(url):
    """Recupera el contenido de Confluence, resolviendo Tiny Links de forma autenticada."""
    if not url or "confluence.tid.es" not in url:
        return ""

    current_url = url
    headers = {
        "Authorization": f"Bearer {CONFLUENCE_PERSONAL_TOKEN}",
        "Accept": "application/json"
    }

    try:
        r = requests.get(current_url, headers=headers, allow_redirects=True, timeout=10)
        current_url = r.url
    except Exception as e:
        print(f"DEBUG: Error resolviendo URL {url}: {e}", flush=True)

    page_id = None
    page_id_match = re.search(r'pageId=(\d+)', current_url)
    if page_id_match:
        page_id = page_id_match.group(1)

    if not page_id:
        view_match = re.search(r'/view/(\d+)', current_url)
        if view_match:
            page_id = view_match.group(1)

    if not page_id:
        pages_match = re.search(r'/pages/(\d+)/', current_url)
        if pages_match:
            page_id = pages_match.group(1)

    if not page_id:
        return ""

    api_url = f"{CONFLUENCE_URL}/rest/api/content/{page_id}?expand=body.storage"
    try:
        res = requests.get(api_url, headers=headers, timeout=30)
        if res.status_code == 200:
            content = res.json().get('body', {}).get('storage', {}).get('value', "")
            return strip_html_tags(content)
    except Exception:
        pass

    return ""


def call_github_models(messages, temperature=0.2, timeout=120):
    """
    Llamada genérica a GitHub Models (Azure inference endpoint que estás usando).
    """
    token = clean_token(GITHUB_TOKEN)
    if not token:
        print("ERROR: El token de GitHub está vacío.", flush=True)
        return None

    url = "https://models.inference.ai.azure.com/chat/completions"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "application/json"
    }

    payload = {
        "model": "gpt-4o",
        "messages": messages,
        "temperature": temperature
    }

    try:
        print(f"DEBUG: Consultando IA con contexto masivo (Token len: {len(token)})...", flush=True)
        res = requests.post(url, json=payload, headers=headers, timeout=timeout)
        if res.status_code == 200:
            return res.json()['choices'][0]['message']['content']
        print(f"DEBUG ERROR IA {res.status_code}: {res.text}", flush=True)
        return None
    except Exception as e:
        print(f"Error grave en la IA: {e}", flush=True)
        return None


def system_contract_no_tables_inventory_and_json():
    """
    Contrato para primera llamada: Inventario + TOTAL + JSON (puede venir incompleto, luego lo completamos).
    Incluye soporte de automatización + heurística de scope reforzada.
    Añade Opción A de KPI: incluir bloque KPI en formatted_description cuando aplique.
    """
    return (
        "Eres un QA Senior experto.\n"
        "RESPONDE SIEMPRE EN ESPAÑOL.\n"
        "\n"
        "OBJETIVO:\n"
        "- Debes producir escenarios System y End2End con criterio.\n"
        "- Usa scope='E2E' cuando el punto del inventario implique flujo completo, navegación multi-pantalla,"
        " integración entre componentes/sistemas, o validaciones de punta a punta.\n"
        "\n"
        "HEURÍSTICA DE SCOPE (OBLIGATORIO):\n"
        "- Si el punto implica UI/UX observable (foco, navegación, animaciones, preview, botones, carrusel), marca scope='E2E'.\n"
        "- Si el punto es puramente de datos/backend (orden, filtros, exclusiones), marca scope='System'.\n"
        "- Intenta que ~30-40% de escenarios sean E2E cuando el feature sea principalmente UI.\n"
        "\n"
        "KPI / RENDIMIENTO (OPCIÓN A, OBLIGATORIO CUANDO APLIQUE):\n"
        "- Cuando el punto del inventario implique transiciones, carga de pantallas, render/preview, animaciones o navegación,\n"
        "  añade al final de formatted_description una sección:\n"
        "  h1. KPI / Rendimiento (si aplica)\n"
        "  ----\n"
        "  * Define 1-3 KPIs con Start/End (evento), método (prioridad logs/telemetría; alternativa driver),\n"
        "    repeticiones (>=5) y criterio (baseline o umbral).\n"
        "- NO inventes herramientas internas; si faltan logs, sugiere 'instrumentar eventos *_start/*_ready'.\n"
        "\n"
        "CONTRATO DE SALIDA (OBLIGATORIO):\n"
        "1) Inventario Técnico como lista numerada simple 1..N (sin subniveles).\n"
        "2) Al final del inventario añade una línea literal exacta: TOTAL_INVENTARIO: N\n"
        "3) Después escribe una línea exacta: JSON_START\n"
        "4) En la siguiente línea, empieza el Array JSON (primer carácter '['). Sin markdown.\n"
        "5) Después del JSON escribe una línea exacta: JSON_END\n"
        "\n"
        "REGLAS:\n"
        "- NO uses corchetes '[' o ']' en el Inventario Técnico.\n"
        "- Cada escenario debe incluir inventory_id.\n"
        "- formatted_description SIN TABLAS.\n"
        "- NO incluyas sección 'Referencias (externas a JIRA)'.\n"
        "- NO pegues enlaces ni tickets en formatted_description.\n"
        "- Mantén formatted_description conciso (<900 caracteres aprox.), además de la sección KPI si aplica.\n"
        "\n"
        "AUTOMATIZACIÓN (OBLIGATORIO EN EL JSON):\n"
        "- Incluye SIEMPRE estos campos en cada escenario:\n"
        "  * automation_candidate: true/false\n"
        "  * automation_type: 'selenium'|'appium'|'api'|'none'\n"
        "  * automation_code: string (si automation_candidate=true debe tener contenido; si false, vacío)\n"
        "- Marca automation_candidate=true SOLO si el escenario es razonablemente automatizable.\n"
        "- Si automation_candidate=true:\n"
        "  * automation_type no puede ser 'none'\n"
        "  * PROHIBIDO devolver placeholders tipo 'selenium_code_for_*', 'TODO', '...' o nombres genéricos.\n"
        "  * automation_code debe ser un ESQUELETO COMPLETO (>= 600 caracteres): imports, setup, navegación,"
        " localizadores, acciones, al menos 3 asserts/checks, y teardown.\n"
        "- Si no puedes producir un esqueleto completo razonable, entonces automation_candidate=false.\n"
        "\n"
        "SCHEMA JSON:\n"
        "[{"
        "\"inventory_id\": 1,"
        "\"main_function\": \"string\","
        "\"test_title\": \"string\","
        "\"scope\": \"System|E2E\","
        "\"formatted_description\": \"string\","
        "\"automation_candidate\": false,"
        "\"automation_type\": \"none\","
        "\"automation_code\": \"\""
        "}]\n"
        "\n"
        "FORMATO formatted_description (Jira Wiki Markup, SIN TABLAS):\n"
        "h1. Breve descripción del test\n----\n<1-2 líneas>\n\n"
        "h1. Pre-requisitos\n----\n* <...>\n\n"
        "h1. Datos de prueba\n----\n* <...>\n\n"
        "h1. Pasos y Resultados Esperados\n----\n"
        "# Acción: <...> | Esperado: <...>\n"
        "# Acción: <...> | Esperado: <...>\n\n"
        "h1. Notas y consideraciones especiales\n----\n* <...>\n"
    )


def system_contract_only_missing_json_no_tables():
    """
    Contrato para llamadas de completado: SOLO JSON entre marcadores, para un rango concreto.
    Debe mantener misma heurística de scope y mismos requisitos de automatización.
    Añade Opción A de KPI: incluir bloque KPI en formatted_description cuando aplique.
    """
    return (
        "Eres un QA Senior experto.\n"
        "RESPONDE SIEMPRE EN ESPAÑOL.\n"
        "\n"
        "HEURÍSTICA DE SCOPE (OBLIGATORIO):\n"
        "- Si el punto implica UI/UX observable (foco, navegación, animaciones, preview, botones, carrusel), marca scope='E2E'.\n"
        "- Si el punto es puramente de datos/backend (orden, filtros, exclusiones), marca scope='System'.\n"
        "- Intenta que ~30-40% de escenarios sean E2E cuando el feature sea principalmente UI.\n"
        "\n"
        "KPI / RENDIMIENTO (OPCIÓN A, OBLIGATORIO CUANDO APLIQUE):\n"
        "- Cuando el escenario implique transiciones/carga/render/animaciones/navegación,\n"
        "  añade al final de formatted_description una sección KPI con Start/End, método y criterio.\n"
        "\n"
        "CONTRATO DE SALIDA (OBLIGATORIO):\n"
        "1) Escribe una línea exacta: JSON_START\n"
        "2) En la siguiente línea, empieza el Array JSON (primer carácter '['). Sin markdown.\n"
        "3) Después del JSON escribe una línea exacta: JSON_END\n"
        "\n"
        "REGLAS:\n"
        "- Devuelve SOLO escenarios del rango inventory_id solicitado.\n"
        "- NO devuelvas inventario.\n"
        "- NO devuelvas texto extra.\n"
        "- formatted_description SIN TABLAS, conciso (<900 caracteres), además de KPI si aplica.\n"
        "- NO incluyas sección 'Referencias (externas a JIRA)'.\n"
        "- NO pegues enlaces ni tickets en formatted_description.\n"
        "\n"
        "AUTOMATIZACIÓN (OBLIGATORIO EN EL JSON):\n"
        "- Incluye SIEMPRE estos campos en cada escenario:\n"
        "  * automation_candidate: true/false\n"
        "  * automation_type: 'selenium'|'appium'|'api'|'none'\n"
        "  * automation_code: string (si automation_candidate=true debe tener contenido; si false, vacío)\n"
        "- Si automation_candidate=true:\n"
        "  * automation_type no puede ser 'none'\n"
        "  * PROHIBIDO placeholders ('selenium_code_for_*', 'TODO', '...').\n"
        "  * automation_code >= 600 caracteres con setup/locators/asserts/teardown.\n"
        "- Si no puedes producirlo, automation_candidate=false.\n"
        "\n"
        "SCHEMA JSON:\n"
        "[{"
        "\"inventory_id\": 1,"
        "\"main_function\": \"string\","
        "\"test_title\": \"string\","
        "\"scope\": \"System|E2E\","
        "\"formatted_description\": \"string\","
        "\"automation_candidate\": false,"
        "\"automation_type\": \"none\","
        "\"automation_code\": \"\""
        "}]\n"
        "\n"
        "FORMATO formatted_description (Jira Wiki Markup, SIN TABLAS):\n"
        "h1. Breve descripción del test\n----\n<1-2 líneas>\n\n"
        "h1. Pre-requisitos\n----\n* <...>\n\n"
        "h1. Datos de prueba\n----\n* <...>\n\n"
        "h1. Pasos y Resultados Esperados\n----\n"
        "# Acción: <...> | Esperado: <...>\n"
        "# Acción: <...> | Esperado: <...>\n\n"
        "h1. Notas y consideraciones especiales\n----\n* <...>\n"
    )


def ask_inventory_and_initial_scenarios(prompt):
    system_content = system_contract_no_tables_inventory_and_json()
    messages = [
        {"role": "system", "content": system_content},
        {"role": "user", "content": prompt}
    ]
    return call_github_models(messages, temperature=0.2, timeout=180)


def ask_missing_scenarios(inventory_text, user_context_prompt, missing_ids_batch):
    """
    Pide SOLO los escenarios faltantes (por IDs concretos, en batch).
    """
    if not missing_ids_batch:
        return None

    start_id = min(missing_ids_batch)
    end_id = max(missing_ids_batch)

    system_content = system_contract_only_missing_json_no_tables()

    user_prompt = (
        "Necesito que completes escenarios de prueba faltantes basados en este inventario.\n\n"
        "INVENTARIO (referencia):\n"
        f"{inventory_text}\n\n"
        "CONTEXTO (fuente de verdad y docs ya consolidados):\n"
        f"{user_context_prompt}\n\n"
        f"Devuelve SOLO los escenarios con inventory_id DESDE {start_id} HASTA {end_id} (inclusive).\n"
        "Cada inventory_id del rango debe aparecer exactamente una vez.\n"
    )

    messages = [
        {"role": "system", "content": system_content},
        {"role": "user", "content": user_prompt}
    ]
    return call_github_models(messages, temperature=0.2, timeout=180)


# --- 3. LÓGICA PRINCIPAL ---
def main():
    print("--- DIAGNÓSTICO DE INICIO ---", flush=True)

    us_key = os.getenv("MANUAL_ISSUE_KEY", "").strip()
    if not us_key:
        print("ERROR: MANUAL_ISSUE_KEY no detectada.", flush=True)
        return

    print(f"--- Procesando: {us_key} ---", flush=True)
    us_data = get_issue(us_key)
    if not us_data:
        return

    us_summary = us_data['fields']['summary']
    us_description_raw = us_data['fields'].get('description', '')
    us_description = strip_html_tags(us_description_raw)

    print(f"Resumen US: {us_summary}", flush=True)

    # --- EXTRACCIÓN DE LINKS (CONFLUENCE Y JIRA) ---
    extra_context = ""

    conf_urls = re.findall(r'https?://confluence\.tid\.es/[^\s\]\)\|\,\>\"\' ]+', us_description_raw)
    if conf_urls:
        print(f"INFO: Detectados {len(conf_urls)} enlaces de Confluence.", flush=True)
        for url in list(set(conf_urls)):
            content = get_confluence_content(url)
            if content:
                print(f"INFO: Contexto extraído de Confluence: {url[:60]}...", flush=True)
                extra_context += f"\nDOCUMENTO CONFLUENCE {url}:\n{content}\n"

    jira_keys_found = re.findall(r'([A-Z][A-Z0-9]+-\d+)', us_description_raw)
    if jira_keys_found:
        print(f"INFO: Detectadas {len(set(jira_keys_found))} referencias a Jira en la descripción.", flush=True)
        for key in list(set(jira_keys_found)):
            if key != us_key:
                print(f"DEBUG: Intentando extraer contenido de referencia Jira: {key}", flush=True)
                issue_data = get_issue(key)
                if issue_data:
                    desc_linked = strip_html_tags(issue_data['fields'].get('description', ''))
                    print(f"INFO: Contexto extraído de ticket vinculado: {key}", flush=True)
                    extra_context += f"\nINFO TICKET VINCULADO {key}:\n{desc_linked[:3000]}\n"

    # --- BÚSQUEDA DE ÉPICA PADRE ---
    epic_key = us_data['fields'].get(ID_CAMPO_EPIC_LINK)
    parent_epic_key = us_key
    if epic_key:
        print(f"INFO: Epic Link detectado: {epic_key}", flush=True)
        epic_data = get_issue(epic_key)
        if epic_data:
            possible_parent = get_parent_epic_key(epic_data)
            parent_epic_key = possible_parent if possible_parent else epic_key
            print(f"INFO: Jerarquía vinculación E2E -> {parent_epic_key}", flush=True)

    # --- DOCUMENTACIÓN DE ÉPICA (Confluence) ---
    parent_data = get_issue(parent_epic_key)
    doc_url = parent_data['fields'].get(ID_CAMPO_DOC_LINK) if parent_data else None
    contexto_epica = ""
    if doc_url:
        print(f"INFO: Consultando documentación de la Épica: {doc_url}", flush=True)
        contexto_epica = get_confluence_content(doc_url)

    full_context = f"{extra_context}\n{contexto_epica}"

    base_user_prompt = f"""
### MANDATO DE GENERACIÓN MASIVA (100% COBERTURA)
Analiza cada frase de la User Story y su contexto técnico vinculado.
1. Elabora un inventario técnico completo de cada parámetro, flujo y configuración encontrada en la US y en los documentos vinculados.
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

### FUENTE DE VERDAD (USER STORY)
Ticket: {us_key}
Resumen: {us_summary}
Descripción: {us_description}

### CONTEXTO ADICIONAL (ENLACES DE JIRA Y CONFLUENCE)
{full_context[:12000]}

### TAREA
Devuelve:
- Inventario Técnico (1..N) + TOTAL_INVENTARIO: N
- JSON_START + Array JSON + JSON_END
- Todo en español
- formatted_description SIN TABLAS y conciso
"""

    # 1) Primera llamada: inventario + JSON (posiblemente incompleto)
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

    # 2) Completar escenarios faltantes en batches, con reintentos
    MAX_COMPLETION_ATTEMPTS = 4
    BATCH_SIZE = 5  # ajustable: 3-6 suele ir bien

    attempt = 0
    while attempt < MAX_COMPLETION_ATTEMPTS:
        attempt += 1

        missing = missing_inventory_ids(n_total, scenarios)
        if not missing:
            break

        print(f"INFO: Faltan {len(missing)} escenarios para completar cobertura 1..{n_total}. Intento {attempt}/{MAX_COMPLETION_ATTEMPTS}",
              flush=True)

        batch = missing[:BATCH_SIZE]
        resp_missing = ask_missing_scenarios(inventory_text, base_user_prompt, batch)
        if not resp_missing:
            print("WARN: No hubo respuesta en completado. Reintentando...", flush=True)
            continue

        dump_raw_response(resp_missing, us_key, suffix=f"missing_attempt_{attempt}")

        _, new_scenarios = extract_analysis_and_json(resp_missing)
        if not new_scenarios:
            print("WARN: No se pudieron parsear escenarios faltantes en este batch. Reintentando...", flush=True)
            continue

        scenarios = normalize_scenarios_merge(scenarios, new_scenarios)

    # 3) Validación final (creamos SOLO si cuadra)
    raw_text_for_validation = inventory_text
    if "TOTAL_INVENTARIO:" not in raw_text_for_validation:
        raw_text_for_validation += f"\nTOTAL_INVENTARIO: {n_total}\n"

    ok, msg = validate_scenarios_coverage(raw_text_for_validation, scenarios)
    if not ok:
        print(f"ERROR: Validación de cobertura fallida: {msg}", flush=True)
        print(f"INFO: Recibidos escenarios={len(scenarios)} de TOTAL_INVENTARIO={n_total}.", flush=True)
        print("INFO: No se crearán Test Cases en Jira para evitar cobertura parcial.", flush=True)
        return

    # 4) Crear TCs (solo Manual, marcando Automation Candidate y añadiendo bloque de automatización si aplica)
    print(f"INFO: Cobertura OK. Procesando {len(scenarios)} escenarios generados...", flush=True)

    scenarios_sorted = sorted(scenarios, key=lambda x: int(x.get("inventory_id", 0)))

    for sc in scenarios_sorted:
        title = sc.get('test_title', 'Test')
        scope = sc.get('scope', 'System')
        is_e2e = scope.lower() in ["e2e", "end2end"]
        link_target = parent_epic_key if is_e2e else us_key

        summary_base = f"[{sc.get('main_function', 'QA')}] {title}"
        print(f"--- Creando: {summary_base} ({scope}) ---", flush=True)

        # Debug blindado (siempre visible)
        print(
            f"DEBUG AUTO inventory_id={sc.get('inventory_id')} "
            f"cand={sc.get('automation_candidate')} type={sc.get('automation_type')} "
            f"code_len={len((sc.get('automation_code') or '').strip())}",
            flush=True
        )

        # Determinar Automation Candidate High/Low/Discarded
        automation_candidate_value = compute_automation_label(sc)

        # Description: manual + KPI (opción A) + bloque automatización (si aplica)
        manual_desc = sc.get('formatted_description', '')
        desc_with_kpi = append_kpi_block_option_a(manual_desc, sc)
        final_desc = append_automation_block_to_description(desc_with_kpi, sc)

        # Crear SIEMPRE TC Manual, con el customfield Automation Candidate
        create_test_case(
            TARGET_PROJECT,
            f"{summary_base} - Manual",
            final_desc,
            link_target,
            scope,
            "Manual",
            automation_candidate_value=automation_candidate_value
        )

    print(f"--- Proceso finalizado para {us_key} ---", flush=True)


if __name__ == "__main__":
    main()
