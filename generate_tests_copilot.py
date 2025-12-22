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
    # Eliminar etiquetas
    clean = re.compile('<.*?>')
    text = re.sub(clean, ' ', text)
    # Normalizar espacios en blanco
    text = re.sub(r'\s+', ' ', text).strip()
    return text

def extract_analysis_and_json(text):
    """
    Separa el análisis técnico previo del bloque JSON.
    Retorna (analisis_texto, lista_escenarios)
    """
    start_index = text.find('[')
    end_index = text.rfind(']')
    
    analysis = ""
    scenarios = []
    
    if start_index != -1:
        analysis = text[:start_index].strip()
        # Limpiar posibles restos de markdown en el análisis
        analysis = re.sub(r'```json|```', '', analysis).strip()
        
        json_str = text[start_index:end_index + 1]
        try:
            scenarios = json.loads(json_str)
        except Exception as e:
            print(f"DEBUG: Error parseando JSON dentro del bloque: {e}")
            
    return analysis, scenarios

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
    
    if labels is None: labels = []
    
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
    if not url or "confluence.tid.es" not in url: return ""
    
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
    """Consulta a la IA (GitHub Models) con mandato de inventario y masividad."""
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
    
    payload = {
        "model": "gpt-4o",
        "messages": [
            {
                "role": "system", 
                "content": (
                    "Eres un QA Senior experto. Tu objetivo es la COBERTURA TOTAL (100%).\n"
                    "SIGUE ESTE PROCESO:\n"
                    "1. Identifica y lista todos los parámetros técnicos, flujos, dispositivos y estados lógicos.\n"
                    "2. Genera una lista EXHAUSTIVA de escenarios (mínimo 15 si es posible). No agrupes validaciones, haz tests atómicos.\n"
                    "3. Para el JSON:\n"
                    "   - 'main_function': Debe ser una categoría corta (ej. 'Navegación', 'Filtrado', 'E2E').\n"
                    "   - 'test_title': Debe ser un título descriptivo claro.\n"
                    "   - 'formatted_description': Usa Jira Wiki Markup en ESPAÑOL con este formato exacto:\n\n"
                    "h1. Breve descripción del test\n----\n(Texto descriptivo)\n\nh1. Pre-requisitos\n----\n||ID||Pre-requisito||\n|1|(Descripción)|\n\nh1. Datos de prueba\n----\n||ID||Datos de prueba||\n|1|(Descripción)|\n\nh1. Pasos y Resultados Esperados\n(puede referenciarse una imagen o tabla para adjuntar bajo esta tabla)\n----\n||ID||Pasos a ejecutar||Resultado esperado||\n|1|(Descripción)|(Resultado)|\n\nh1. Notas y consideraciones especiales\n----\n||ID||Descripción||\n|1|(Descripción)|\n\nh1. Referencias (externas a JIRA)\n----\n||ID||Descripción||Enlace||\n|1|(Descripción)|(URL)|\n\n"
                    "4. Responde primero con tu análisis/inventario técnico en texto libre y luego el bloque JSON."
                )
            },
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
    if not us_data: return
        
    us_summary = us_data['fields']['summary']
    # Limpiamos el HTML de la descripción para mejorar detección y tokens
    us_description_raw = us_data['fields'].get('description', '')
    us_description = strip_html_tags(us_description_raw)
    
    print(f"Resumen US: {us_summary}")

    # --- EXTRACCIÓN DE LINKS (CONFLUENCE Y JIRA) ---
    extra_context = ""
    # 1. Links de Confluence
    conf_urls = re.findall(r'https?://confluence\.tid\.es/[^\s\]\)\|\,\>]+', us_description_raw)
    if conf_urls:
        print(f"INFO: Detectados {len(conf_urls)} enlaces de Confluence.")
        for url in list(set(conf_urls)):
            content = get_confluence_content(url)
            if content:
                print(f"INFO: Contexto extraído de Confluence: {url[:60]}...")
                extra_context += f"\n[DOC CONFLUENCE {url}]:\n{content}\n"

    # 2. Links de Jira (Tasks, Bugs, otras US)
    jira_keys = re.findall(r'jira\.tid\.es/browse/([A-Z0-9]+-\d+)', us_description_raw)
    if jira_keys:
        print(f"INFO: Detectados {len(jira_keys)} enlaces de Jira en la descripción.")
        for key in list(set(jira_keys)):
            if key != us_key:
                issue_data = get_issue(key)
                if issue_data:
                    desc = strip_html_tags(issue_data['fields'].get('description', ''))
                    print(f"INFO: Contexto extraído de Jira Task: {key}")
                    extra_context += f"\n[INFO TAREA VINCULADA {key}]:\n{desc[:2000]}\n"

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

    # --- DOCUMENTACIÓN DE ÉPICA ---
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
    Analiza cada frase de la User Story y su contexto técnico. 
    1. Elabora un inventario técnico de parámetros (ej. dispositivos, tipos de perfil, lógica de carruseles, filtrados).
    2. Genera una lista EXTENSA de al menos 15 escenarios específicos. No te limites, sé exhaustivo.

    ### FUENTE DE VERDAD (USER STORY)
    Ticket: {us_key}
    Resumen: {us_summary}
    Descripción: {us_description}

    ### CONTEXTO ADICIONAL (ENLACES Y ÉPICAS)
    {full_context[:12000]}

    ### TAREA
    Devuelve:
    - Primero el 'Inventario Técnico'.
    - Luego el Array JSON con los escenarios.
    
    Para cada 'formatted_description', usa esta estructura en ESPAÑOL:
    h1. Breve descripción del test
    ----
    h1. Pre-requisitos
    ----
    h1. Datos de prueba
    ----
    h1. Pasos y Resultados Esperados
    (puede referenciarse una imagen o tabla para adjuntar bajo esta tabla)
    ----
    ||ID||Pasos a ejecutar||Resultado esperado||
    h1. Notas y consideraciones especiales
    ----
    h1. Referencias (externas a JIRA)
    ----
    """
    
    respuesta = ask_copilot(prompt)
    if not respuesta: return

    # Separar inventario y JSON
    analisis, scenarios = extract_analysis_and_json(respuesta)
    
    if analisis:
        print("\n" + "="*50)
        print("INVENTARIO TÉCNICO CONSIDERADO POR LA IA:")
        print(analisis)
        print("="*50 + "\n")

    if scenarios:
        print(f"INFO: Procesando {len(scenarios)} escenarios generados...")
        for sc in scenarios:
            title = sc.get('test_title', 'Test')
            scope = sc.get('scope', 'System')
            is_e2e = scope.lower() in ["e2e", "end2end"]
            link_target = parent_epic_key if is_e2e else us_key
            
            # Construcción del summary: [Categoría] Título
            summary_base = f"[{sc.get('main_function', 'QA')}] {title}"
            print(f"--- Creando: {summary_base} ({scope}) ---")
            
            create_test_case(TARGET_PROJECT, f"{summary_base} - Manual", sc.get('formatted_description', '') + JIRA_WIKI_TIPS_PANEL, link_target, scope, "Manual")
            
            if GENERATE_AUTOMATION:
                auto_desc = f"{sc.get('formatted_description', '')}\n\nh1. 5. Automatización\n{{code:python}}\n{sc.get('automation_code', '')}\n{{code}}"
                create_test_case(TARGET_PROJECT, f"{summary_base} - Automatic", auto_desc, link_target, scope, "Automatic")
    else:
        print("ERROR: No se pudo procesar el JSON. Revisa la respuesta cruda.")
        print(f"RESPUESTA CRUDA:\n{respuesta}")
            
    print(f"--- Proceso finalizado para {us_key} ---")

if __name__ == "__main__":
    main()