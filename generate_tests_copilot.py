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

# --- 1. CONFIGURACIÓN DE ENTORNO (Sincronizado con MCP Atlassian) ---
JIRA_URL = os.getenv("JIRA_URL", "https://jira.tid.es/").rstrip('/')
JIRA_USERNAME = os.getenv("JIRA_USERNAME", "id02621")
JIRA_PERSONAL_TOKEN = os.getenv("JIRA_PERSONAL_TOKEN")

CONFLUENCE_URL = os.getenv("CONFLUENCE_URL", "https://confluence.tid.es/").rstrip('/')
CONFLUENCE_USERNAME = os.getenv("CONFLUENCE_USERNAME", "id02621")
CONFLUENCE_PERSONAL_TOKEN = os.getenv("CONFLUENCE_PERSONAL_TOKEN")

# Proyecto destino para los Test Cases (MULTISTC-31379)
TARGET_PROJECT = os.getenv("TARGET_PROJECT", "MULTISTC-31379")

# Token de GitHub para acceso a Copilot
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN") 

# IDs de campos personalizados TID
ID_CAMPO_EPIC_LINK = "customfield_10001" 
ID_CAMPO_DOC_LINK = "customfield_22398"  
ID_CAMPO_TEST_SCOPE = "customfield_10163"    # end2end / system
ID_CAMPO_EXECUTION_MODE = "customfield_10150" # Manual / Automatic

# Headers de Jira
jira_headers = {
    "Authorization": f"Bearer {JIRA_PERSONAL_TOKEN}",
    "Accept": "application/json",
    "Content-Type": "application/json"
}

# --- PLANTILLA WIKI MARKUP (TIPS FINALES) ---
JIRA_WIKI_TIPS_PANEL = """
{panel:title=TIPS to create a good Test Plan}
* Don't forget NON-FUNCTIONAL tests (performance, usability, interoperability, security)
* Don't forget cross-device tests
* Use an AI tool like Copilot for assistance to copy/paste with this template
* If you are a System Test Case (User Story that belongs to an Epic or Task), you must be linked to a User Story or Task (Test Case tests User Story/Task)
* If you are an E2E Test Case (Epic or individual User Story), you must be linked to an Epic or to the parent of the User Story (Test Case tests Epic/User Story)
{panel}
"""

def get_issue(issue_key):
    """Obtiene el JSON de una incidencia en Jira."""
    try:
        url = f"{JIRA_URL}/rest/api/2/issue/{issue_key}"
        res = requests.get(url, headers=jira_headers, timeout=30)
        if res.status_code != 200:
            print(f"Error Jira ({issue_key}): {res.status_code} - {res.text}")
            return None
        return res.json()
    except Exception as e:
        print(f"Excepción conectando a Jira: {e}")
        return None

def get_parent_epic_key(epic_data):
    """Navega por los enlaces buscando la relación 'is child of'."""
    links = epic_data['fields'].get('issuelinks', [])
    for link in links:
        l_type = link.get('type', {})
        if (link.get('inwardIssue') and l_type.get('inward') == 'is child of'):
            return link['inwardIssue']['key']
        if (link.get('outwardIssue') and l_type.get('outward') == 'is child of'):
            return link['outwardIssue']['key']
    return None

def create_test_case(project_key, summary, description, target_link_key, scope="system", mode="Manual", labels=None):
    """Crea el Test Case en el proyecto destino y lo vincula."""
    url = f"{JIRA_URL}/rest/api/2/issue"
    scope_value = "end2end" if scope.lower() == "e2e" else "system"
    if labels is None: labels = []
    
    payload = {
        "fields": {
            "project": {"key": project_key},
            "summary": summary,
            "description": description,
            "issuetype": {"name": "testCase"},
            "labels": labels,
            ID_CAMPO_TEST_SCOPE: {"value": scope_value},
            ID_CAMPO_EXECUTION_MODE: {"value": mode}
        }
    }
    
    res = requests.post(url, json=payload, headers=jira_headers)
    if res.status_code == 201:
        tc_key = res.json()['key']
        print(f"Éxito: TC {tc_key} ({mode}) creado en {project_key} vinculado a {target_link_key}")
        link_issues(target_link_key, tc_key)
        return tc_key
    print(f"Error creando TC {mode}: {res.status_code} - {res.text}")
    return None

def link_issues(parent_key, tc_key):
    """Establece el vínculo: US (is tested by) -> TC (tests)."""
    url = f"{JIRA_URL}/rest/api/2/issueLink"
    payload = {
        "type": {"name": "Test"}, 
        "inwardIssue": {"key": tc_key},
        "outwardIssue": {"key": parent_key}
    }
    requests.post(url, json=payload, headers=jira_headers)

def get_confluence_content(url):
    """Extrae texto de Confluence."""
    if not url or "confluence.tid.es" not in url: return ""
    page_id_match = re.search(r'pageId=(\d+)', url)
    page_id = page_id_match.group(1) if page_id_match else None
    if not page_id:
        view_match = re.search(r'/view/(\d+)', url)
        page_id = view_match.group(1) if view_match else None
    if not page_id: return ""
    
    api_url = f"{CONFLUENCE_URL}/rest/api/content/{page_id}?expand=body.storage"
    headers = {
        "Authorization": f"Bearer {CONFLUENCE_PERSONAL_TOKEN}",
        "Accept": "application/json"
    }
    try:
        res = requests.get(api_url, headers=headers, timeout=30)
        if res.status_code == 200:
            return res.json().get('body', {}).get('storage', {}).get('value', "")
    except:
        pass
    return ""

def ask_copilot(prompt):
    """Consulta a Copilot definiendo el rol de QA Senior con Debugging."""
    print("DEBUG: Iniciando llamada a Copilot API...")
    if not GITHUB_TOKEN or "TU_GITHUB_PAT" in GITHUB_TOKEN:
        print("DEBUG ERROR: GITHUB_TOKEN no configurado o inválido.")
        return None

    url = "https://api.githubcopilot.com/chat/completions"
    headers = {
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "Content-Type": "application/json",
        "Editor-Version": "vscode/1.95.3"
    }
    payload = {
        "model": "gpt-4",
        "messages": [
            {
                "role": "system", 
                "content": (
                    "Eres un Ingeniero de QA Senior experto en testing funcional, E2E y Selenium. "
                    "Diseñas Test Cases siguiendo el esquema de tablas de Jira Wiki. "
                    "Analiza seguridad, UI y regresión. Usa 'Assumptions' si falta información."
                )
            },
            {"role": "user", "content": prompt}
        ],
        "temperature": 0.1
    }
    
    try:
        res = requests.post(url, json=payload, headers=headers, timeout=60)
        print(f"DEBUG: Status Code Copilot: {res.status_code}")
        if res.status_code == 200:
            return res.json()['choices'][0]['message']['content']
        else:
            print(f"DEBUG ERROR: {res.status_code} - {res.text}")
            return None
    except Exception as e:
        print(f"DEBUG EXCEPCIÓN: {str(e)}")
        return None

def main():
    print("--- DIAGNÓSTICO DE INICIO ---")
    print(f"¿Archivo .env cargado?: {env_loaded}")
    print(f"GITHUB_TOKEN detectado: {'SÍ' if GITHUB_TOKEN and 'TU_GITHUB_PAT' not in GITHUB_TOKEN else 'NO'}")
    
    us_key = os.getenv("MANUAL_ISSUE_KEY")
    if not us_key:
        try:
            payload_str = os.getenv("JIRA_PAYLOAD")
            if payload_str:
                us_key = json.loads(payload_str).get("issue_key")
        except: pass

    if not us_key:
        print("ERROR: No se ha detectado ninguna 'issue_key'.")
        return

    print(f"--- Iniciando procesamiento QA Senior para: {us_key} ---")
    us_data = get_issue(us_key)
    if not us_data: 
        print("ERROR: No se pudo obtener la data de la US.")
        return
        
    us_summary = us_data['fields']['summary']
    print(f"Contexto US: {us_summary}")

    # Navegación US -> Epic -> Parent Epic
    epic_key = us_data['fields'].get(ID_CAMPO_EPIC_LINK)
    parent_epic_key = us_key
    if epic_key:
        epic_data = get_issue(epic_key)
        if epic_data:
            parent_epic_key = get_parent_epic_key(epic_data) or epic_key

    # Doc de Confluence
    parent_data = get_issue(parent_epic_key)
    doc_url = parent_data['fields'].get(ID_CAMPO_DOC_LINK) if parent_data else None
    contexto = get_confluence_content(doc_url)
    print(f"Contexto Confluence: {'OK' if contexto else 'Vacío'}")

    prompt = f"""
    ### CONTEXTO
    User Story: {us_summary}
    Description: {us_data['fields']['description']}
    Arquitectura: {contexto[:2500]}

    ### TAREA
    Genera 3 escenarios críticos. Para cada uno, rellena estas tablas Jira Wiki:

    h1. Test short description
    ----
    (Resumen del objetivo)

    h1. Pre-requisites
    ----
    ||ID||Pre-requisite||
    |1|(Detalle)|

    h1. Test Data
    ----
    ||ID||Test Data||
    |1|(Dato)|

    h1. Steps & Expected Results
    ----
    ||ID||Steps to Execute||Expected result||
    |1|(Acción)|(Resultado)|

    h1. Notes and Special Considerations
    ----
    ||ID||Description||
    |1|(Nota)|

    h1. References (external to JIRA)
    ----
    ||ID||Description||Link||
    |1|Contexto Confluence|{doc_url if doc_url else "N/A"}|

    ### REQUISITOS:
    - Título: "[Función Principal] Título descriptivo".
    - Código Selenium Python funcional para cada caso.
    - Clasifica scope: 'E2E' (si cruza sistemas) o 'system' (funcional).

    Responde SOLO JSON Array:
    [
      {{
        "main_function": "...",
        "test_title": "...",
        "formatted_description": "...",
        "automation_code": "...",
        "scope": "E2E"
      }}
    ]
    """
    
    print("Consultando a la IA (esto puede tardar unos 30-45 segundos)...")
    respuesta = ask_copilot(prompt)
    
    if not respuesta:
        print("ERROR: La IA no devolvió respuesta o hubo un error en la llamada.")
        return

    try:
        clean_json = re.sub(r'```json|```', '', respuesta).strip()
        scenarios = json.loads(clean_json)
        for sc in scenarios:
            summary_base = f"[{sc['main_function']}] {sc['test_title']}"
            scope = sc.get('scope', 'system')
            link_target = parent_epic_key if scope == "E2E" else us_key
            
            # Crear Manual en MULTISTC
            create_test_case(TARGET_PROJECT, f"{summary_base} - Manual", sc['formatted_description'] + JIRA_WIKI_TIPS_PANEL, link_target, scope, "Manual", ["IA_manual"])
            
            # Crear Automático en MULTISTC
            auto_desc = f"{sc['formatted_description']}\n\nh1. 5. Automation (Selenium)\n{{code:python}}\n{sc['automation_code']}\n{{code}}"
            create_test_case(TARGET_PROJECT, f"{summary_base} - Automatic", auto_desc, link_target, scope, "Automatic", ["IA_automatico"])
        print(f"--- Proceso finalizado con éxito para {us_key} ---")
    except Exception as e:
        print(f"Error parseando JSON: {e}")
        print(f"Respuesta cruda de la IA: {respuesta}")

if __name__ == "__main__":
    main()