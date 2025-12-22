import os
import requests
import json
import sys
import re
import time

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


def dump_raw_response(text, us_key):
    """
    Guarda la respuesta cruda de la IA en un fichero para depuración.
    """
    ts = time.strftime("%Y%m%d_%H%M%S")
    filename = f"debug_raw_ai_response_{us_key}_{ts}.txt"
    try:
        with open(filename, "w", encoding="utf-8") as f:
            f.write(text)
        print(f"DEBUG: Respuesta cruda de la IA guardada en: {filename}")
    except Exception as e:
        print(f"DEBUG ERROR: No se pudo guardar la respuesta cruda: {e}")


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


def extract_analysis_and_json(text):
    """
    Separa el análisis técnico previo del bloque JSON.

    Nuevo método robusto:
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
            print(f"DEBUG: Error parseando JSON entre marcadores: {e}")
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
            print(f"DEBUG: Error parseando JSON dentro del bloque (fallback): {e}")

    return analysis, scenarios


def validate_scenarios_coverage(raw_text, scenarios):
    """
    Valida coherencia:
    - TOTAL_INVENTARIO: N existe y es int.
    - len(scenarios) == N
    - inventory_id existe en todos, es int, y cubre exactamente 1..N sin duplicados.
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

        # Campos mínimos esperados
        for req in ["main_function", "test_title", "scope", "formatted_description"]:
            if req not in sc:
                return False, f"Falta '{req}' en escenario con inventory_id={sc.get('inventory_id')}."

    ids_sorted = sorted(ids)
    expected = list(range(1, n + 1))
    if ids_sorted != expected:
        return False, f"inventory_id no cubre 1..{n} exactamente. Recibidos: {ids_sorted}"

    return True, "OK"


def missing_inventory_ids(raw_text, scenarios):
    """
    Devuelve (N, missing_ids) donde missing_ids son los inventory_id faltantes para cubrir 1..N.
    Si no existe TOTAL_INVENTARIO devuelve (None, None).
    """
    n = extract_total_inventory(raw_text)
    if n is None:
        return None, None

    have = set()
    if isinstance(scenarios, list):
        for sc in scenarios:
            if isinstance(sc, dict) and "inventory_id" in sc:
                try:
                    have.add(int(sc.get("inventory_id")))
                except Exception:
                    pass

    expected = set(range(1, n + 1))
    missing = sorted(list(expected - have))
    return n, missing


def merge_scenarios(existing, new_items):
    """
    Merge por inventory_id. Si hay conflicto, el nuevo pisa el existente.
    Devuelve lista ordenada por inventory_id.
    """
    by_id = {}

    if isinstance(existing, list):
        for sc in existing:
            if isinstance(sc, dict) and "inventory_id" in sc:
                try:
                    by_id[int(sc["inventory_id"])] = sc
                except Exception:
                    pass

    if isinstance(new_items, list):
        for sc in new_items:
            if isinstance(sc, dict) and "inventory_id" in sc:
                try:
                    by_id[int(sc["inventory_id"])] = sc
                except Exception:
                    pass

    merged = [by_id[k] for k in sorted(by_id.keys())]
    return merged


def build_repair_prompt(us_key, total_n, missing_ids, original_prompt):
    """
    Prompt de reparación: pide SOLO los inventory_id faltantes, JSON-only entre marcadores.
    """
    missing_str = ", ".join(str(x) for x in missing_ids)
    return f"""
FALLO DE COBERTURA DETECTADO.

Contexto:
- Ticket principal: {us_key}
- TOTAL_INVENTARIO esperado: {total_n}
- inventory_id faltantes: {missing_str}

INSTRUCCIONES (OBLIGATORIAS):
- NO devuelvas Inventario Técnico.
- Devuelve ÚNICAMENTE el JSON entre marcadores, exactamente así:
  JSON_START
  [
    ...objetos...
  ]
  JSON_END
- El JSON debe contener EXACTAMENTE {len(missing_ids)} objetos.
- Cada objeto debe tener un inventory_id que esté en la lista de faltantes (solo esos, sin extras).
- Mantén el mismo schema: inventory_id, main_function, test_title, scope, formatted_description.
- Todo en español (main_function, test_title, formatted_description).

RECUERDA LAS REGLAS DE formatted_description:
- Estructura fija con tablas.
- NO incluyas sección 'Referencias (externas a JIRA)'.
- NO pegues enlaces ni tickets dentro de formatted_description.
- Tabla de pasos: cabecera '||', filas con SOLO '|', 3 columnas exactas.

Tarea original (para conservar contexto):
{original_prompt}
""".strip()


# --- 1. CONFIGURACIÓN DE ENTORNO ---
JIRA_URL = os.getenv("JIRA_URL", "https://jira.tid.es/").strip().rstrip('/')
JIRA_USERNAME = os.getenv("JIRA_USERNAME", "id02621").strip()
JIRA_PERSONAL_TOKEN = clean_token(os.getenv("JIRA_PERSONAL_TOKEN", ""))

CONFLUENCE_URL = os.getenv("CONFLUENCE_URL", "https://confluence.tid.es/").strip().rstrip('/')
CONFLUENCE_PERSONAL_TOKEN = clean_token(os.getenv("CONFLUENCE_PERSONAL_TOKEN", ""))

TARGET_PROJECT = os.getenv("TARGET_PROJECT", "MULTISTC").strip()
GITHUB_TOKEN = clean_token(os.getenv("GITHUB_TOKEN", ""))

# CONFIGURACIÓN DE PRUEBAS
GENERATE_AUTOMATION = False

# IDs de campos personalizados TID
ID_CAMPO_EPIC_LINK = "customfield_11600"
ID_CAMPO_DOC_LINK = "customfield_22398"
ID_CAMPO_TEST_SCOPE = "customfield_10163"
ID_CAMPO_EXECUTION_MODE = "customfield_10150"

jira_headers = {
    "Authorization": f"Bearer {JIRA_PERSONAL_TOKEN}",
    "Accept": "application/json",
    "Content-Type": "application/json"
}

# Plantilla de TIPS
JIRA_WIKI_TIPS_PANEL = """
{panel:title=TIPS to create a good Test Plan}
* Don't forget NON-FUNCTIONAL tests (performance, usability, interoperability, security)
* Don't forget cross-device tests
* Use an AI tool like Copilot for assistance to copy/paste with this template
* If you are a System Test Case (User Story that belongs to an Epic or Task), you must be linked to a User Story or Task (Test Case tests User Story/Task)
* If you are an E2E Test Case (Epic or individual User Story), you must be linked to an Epic or to the parent of the User Story (Test Case tests Epic/User Story)
{panel}
"""


# --- 2. FUNCIONES DE COMUNICACIÓN ---

def get_issue(issue_key):
    """Obtiene los datos de un ticket de Jira."""
    try:
        url = f"{JIRA_URL}/rest/api/2/issue/{issue_key}"
        res = requests.get(url, headers=jira_headers, timeout=30)
        if res.status_code != 200:
            print(f"Error Jira ({issue_key}): {res.status_code}")
            return None
        return res.json()
    except Exception as e:
        print(f"Excepción grave en Jira: {e}")
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


def create_test_case(project_key, summary, description, target_link_key, scope="System", mode="Manual", labels=None):
    """Crea un nuevo Test Case en Jira."""
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
            ID_CAMPO_EXECUTION_MODE: {"value": mode_value}
        }
    }

    res = requests.post(url, json=payload, headers=jira_headers)
    if res.status_code == 201:
        tc_key = res.json()['key']
        print(f"Éxito: TC {tc_key} ({mode_value}/{scope_value}) creado.")
        link_issues(target_link_key, tc_key)
        return tc_key
    print(f"Error creando TC {mode_value}: {res.status_code} - {res.text}")
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
        print(f"Vínculo establecido: {tc_key} ---[tests]---> {parent_key}")
    else:
        print(f"Error vinculando {tc_key} con {parent_key}: {res.status_code} - {res.text}")


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
        print(f"DEBUG: Error resolviendo URL {url}: {e}")

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


def ask_copilot(prompt):
    """Consulta a la IA (GitHub Models) con contrato estricto + marcadores robustos."""
    token = clean_token(GITHUB_TOKEN)
    if not token:
        print("ERROR: El token de GitHub está vacío.")
        return None

    url = "https://models.inference.ai.azure.com/chat/completions"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "application/json"
    }

    # IMPORTANTÍSIMO:
    # - Marcadores JSON_START / JSON_END
    # - Español obligatorio
    # - formatted_description con estructura fija y SIN sección "Referencias (externas a JIRA)"
    # - coverage exacta 1:1 con inventory_id 1..N

    system_content = (
        "Eres un QA Senior experto.\n"
        "RESPONDE SIEMPRE EN ESPAÑOL (títulos, descripciones, pasos, notas).\n"
        "\n"
        "CONTRATO DE SALIDA (OBLIGATORIO):\n"
        "A) Devuelve SIEMPRE dos secciones en este orden:\n"
        "   1) Inventario Técnico (texto).\n"
        "   2) JSON entre marcadores.\n"
        "B) En el Inventario Técnico NO uses corchetes '[' o ']' en ningún caso.\n"
        "C) Inventario Técnico debe ser lista numerada simple 1..N (sin subniveles).\n"
        "D) Al final del Inventario añade una línea literal exacta: TOTAL_INVENTARIO: N\n"
        "E) Después del inventario escribe en una línea exacta: JSON_START\n"
        "F) En la siguiente línea empieza el Array JSON (primer carácter '['). Sin markdown.\n"
        "G) Después del JSON escribe en una línea exacta: JSON_END\n"
        "\n"
        "REGLAS DE COBERTURA (OBLIGATORIAS):\n"
        "- El Array JSON debe contener EXACTAMENTE N escenarios.\n"
        "- Cada escenario cubre EXACTAMENTE 1 ítem del inventario.\n"
        "- Cada escenario incluye inventory_id con el número del ítem cubierto (1..N).\n"
        "- Debe existir 1 y solo 1 escenario por cada inventory_id del 1 al N.\n"
        "- No escribas NADA entre JSON_START y el '[' ni entre el ']' y JSON_END.\n"
        "\n"
        "SCHEMA JSON OBLIGATORIO:\n"
        "[\n"
        "  {\n"
        "    \"inventory_id\": 1,\n"
        "    \"main_function\": \"string\",\n"
        "    \"test_title\": \"string\",\n"
        "    \"scope\": \"System|E2E\",\n"
        "    \"formatted_description\": \"string\"\n"
        "  }\n"
        "]\n"
        "\n"
        "FORMATO OBLIGATORIO de formatted_description (Jira Wiki Markup en español):\n"
        "h1. Breve descripción del test\n"
        "----\n"
        "h1. Pre-requisitos\n"
        "----\n"
        "||ID||Pre-requisite||\n"
        "(FILAS: usa SOLO '|' en cada fila, ej: |1|texto|)\n"
        "h1. Datos de prueba\n"
        "----\n"
        "||ID||Test Data||\n"
        "(FILAS: usa SOLO '|' en cada fila, ej: |1|texto|)\n"
        "h1. Pasos y Resultados Esperados\n"
        "----\n"
        "||ID||Steps to Execute||Expected result||\n"
        "IMPORTANTE TABLA PASOS:\n"
        "- La cabecera usa '||'.\n"
        "- Las filas usan SOLO '|' (NUNCA '||' dentro de una fila).\n"
        "- EXACTAMENTE 3 columnas por fila: |ID|Steps to Execute|Expected result|\n"
        "h1. Notas y consideraciones especiales\n"
        "----\n"
        "IMPORTANTE:\n"
        "- NO incluyas la sección 'Referencias (externas a JIRA)'.\n"
        "- NO pegues enlaces ni tickets en formatted_description.\n"
        "- Mantén cada formatted_description por debajo de ~1200 caracteres sin romper tablas.\n"
        "\n"
        "VALIDACIÓN INTERNA ANTES DE RESPONDER:\n"
        "- Asegura TOTAL_INVENTARIO: N correcto.\n"
        "- Asegura JSON con EXACTAMENTE N escenarios.\n"
        "- Asegura inventory_id 1..N sin huecos ni duplicados.\n"
        "- Asegura que cada objeto contiene inventory_id, main_function, test_title, scope, formatted_description.\n"
    )

    payload = {
        "model": "gpt-4o",
        "messages": [
            {"role": "system", "content": system_content},
            {"role": "user", "content": prompt}
        ],
        "temperature": 0.2
    }

    try:
        print(f"DEBUG: Consultando IA con contexto masivo (Token len: {len(token)})...")
        res = requests.post(url, json=payload, headers=headers, timeout=90)
        if res.status_code == 200:
            return res.json()['choices'][0]['message']['content']
        else:
            print(f"DEBUG ERROR IA {res.status_code}: {res.text}")
            return None
    except Exception as e:
        print(f"Error grave en la IA: {e}")
        return None


# --- 3. LÓGICA PRINCIPAL ---

def main():
    print("--- DIAGNÓSTICO DE INICIO ---")

    us_key = os.getenv("MANUAL_ISSUE_KEY", "").strip()
    if not us_key:
        print("ERROR: MANUAL_ISSUE_KEY no detectada.")
        return

    print(f"--- Procesando: {us_key} ---")
    us_data = get_issue(us_key)
    if not us_data:
        return

    us_summary = us_data['fields']['summary']
    us_description_raw = us_data['fields'].get('description', '')
    us_description = strip_html_tags(us_description_raw)

    print(f"Resumen US: {us_summary}")

    # --- EXTRACCIÓN DE LINKS (CONFLUENCE Y JIRA) ---
    extra_context = ""

    # 1. Links de Confluence (Regex mejorada)
    conf_urls = re.findall(r'https?://confluence\.tid\.es/[^\s\]\)\|\,\>\"\' ]+', us_description_raw)
    if conf_urls:
        print(f"INFO: Detectados {len(conf_urls)} enlaces de Confluence.")
        for url in list(set(conf_urls)):
            content = get_confluence_content(url)
            if content:
                print(f"INFO: Contexto extraído de Confluence: {url[:60]}...")
                # Evitar corchetes en etiquetas para no confundir al modelo/extractor
                extra_context += f"\nDOCUMENTO CONFLUENCE {url}:\n{content}\n"

    # 2. Links de Jira (claves tipo PROJ-123)
    jira_keys_found = re.findall(r'([A-Z][A-Z0-9]+-\d+)', us_description_raw)
    if jira_keys_found:
        print(f"INFO: Detectadas {len(set(jira_keys_found))} referencias a Jira en la descripción.")
        for key in list(set(jira_keys_found)):
            if key != us_key:
                print(f"DEBUG: Intentando extraer contenido de referencia Jira: {key}")
                issue_data = get_issue(key)
                if issue_data:
                    desc_linked = strip_html_tags(issue_data['fields'].get('description', ''))
                    print(f"INFO: Contexto extraído de ticket vinculado: {key}")
                    extra_context += f"\nINFO TICKET VINCULADO {key}:\n{desc_linked[:3000]}\n"

    # --- BÚSQUEDA DE ÉPICA PADRE ---
    epic_key = us_data['fields'].get(ID_CAMPO_EPIC_LINK)
    parent_epic_key = us_key
    if epic_key:
        print(f"INFO: Epic Link detectado: {epic_key}")
        epic_data = get_issue(epic_key)
        if epic_data:
            possible_parent = get_parent_epic_key(epic_data)
            parent_epic_key = possible_parent if possible_parent else epic_key
            print(f"INFO: Jerarquía vinculación E2E -> {parent_epic_key}")

    # --- DOCUMENTACIÓN DE ÉPICA (Confluence) ---
    parent_data = get_issue(parent_epic_key)
    doc_url = parent_data['fields'].get(ID_CAMPO_DOC_LINK) if parent_data else None
    contexto_epica = ""
    if doc_url:
        print(f"INFO: Consultando documentación de la Épica: {doc_url}")
        contexto_epica = get_confluence_content(doc_url)

    # --- GENERACIÓN ---
    full_context = f"{extra_context}\n{contexto_epica}"

    prompt = f"""
### MANDATO DE GENERACIÓN MASIVA (100% COBERTURA)
Analiza cada frase de la User Story y su contexto técnico vinculado.
1. Elabora un inventario técnico completo de cada parámetro, flujo y configuración encontrada en la US y en los documentos vinculados.
2. Genera una lista EXTENSA de escenarios específicos. NO TE SALTES NINGÚN PUNTO DEL INVENTARIO.
3. Debe existir correspondencia exacta 1:1 entre inventario y escenarios.

### FUENTE DE VERDAD (USER STORY)
Ticket: {us_key}
Resumen: {us_summary}
Descripción: {us_description}

### CONTEXTO ADICIONAL (ENLACES DE JIRA Y CONFLUENCE)
{full_context[:12000]}

### TAREA
Devuelve:
- Primero el 'Inventario Técnico' (lista numerada simple 1..N, sin subniveles).
- Al final del inventario añade: TOTAL_INVENTARIO: N
- Luego, JSON entre marcadores (JSON_START ... JSON_END) con EXACTAMENTE N escenarios.
- 1 escenario por inventory_id 1..N.
- Todo en español.
"""

    respuesta = ask_copilot(prompt)
    if not respuesta:
        return

    # DEBUG: guardar respuesta cruda completa (clave para investigar cualquier desviación)
    dump_raw_response(respuesta, us_key)

    # Separar inventario y JSON
    analisis, scenarios = extract_analysis_and_json(respuesta)

    if analisis:
        print("\n" + "=" * 50)
        print("INVENTARIO TÉCNICO CONSIDERADO POR LA IA:")
        print(analisis)
        print("=" * 50 + "\n")

    # Validación dura: si no cuadra, NO creamos tickets
    ok, msg = validate_scenarios_coverage(respuesta, scenarios)
    if not ok:
        print(f"ERROR: Validación de cobertura fallida: {msg}")
        print("INFO: No se crearán Test Cases en Jira para evitar cobertura parcial.")
        return

    if scenarios:
        print(f"INFO: Procesando {len(scenarios)} escenarios generados...")

        # Ordenar por inventory_id para que cree tickets de forma estable
        scenarios_sorted = sorted(scenarios, key=lambda x: int(x.get("inventory_id", 0)))

        for sc in scenarios_sorted:
            title = sc.get('test_title', 'Test')
            scope = sc.get('scope', 'System')
            is_e2e = scope.lower() in ["e2e", "end2end"]
            link_target = parent_epic_key if is_e2e else us_key

            # Construcción del summary: [Categoría] Título
            summary_base = f"[{sc.get('main_function', 'QA')}] {title}"
            print(f"--- Creando: {summary_base} ({scope}) ---")

            create_test_case(
                TARGET_PROJECT,
                f"{summary_base} - Manual",
                sc.get('formatted_description', '') + JIRA_WIKI_TIPS_PANEL,
                link_target,
                scope,
                "Manual"
            )

            if GENERATE_AUTOMATION:
                auto_desc = (
                    f"{sc.get('formatted_description', '')}\n\n"
                    f"h1. 5. Automatización\n{{code:python}}\n{sc.get('automation_code', '')}\n{{code}}"
                )
                create_test_case(
                    TARGET_PROJECT,
                    f"{summary_base} - Automatic",
                    auto_desc,
                    link_target,
                    scope,
                    "Automatic"
                )
    else:
        print("ERROR: No se pudo procesar el JSON. Revisa la respuesta cruda.")
        print(f"RESPUESTA CRUDA:\n{respuesta}")

    print(f"--- Proceso finalizado para {us_key} ---")


if __name__ == "__main__":
    main()
