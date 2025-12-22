import os
import requests
import json
import sys
import re

# --- CONFIGURACIÓN DE ENTORNO ---
JIRA_URL = os.getenv("JIRA_URL", "https://jira.tid.es").rstrip('/')
CONFLUENCE_URL = os.getenv("CONFLUENCE_URL", "https://confluence.tid.es").rstrip('/')
JIRA_TOKEN = os.getenv("JIRA_PERSONAL_TOKEN")
CONFLUENCE_TOKEN = os.getenv("CONFLUENCE_PERSONAL_TOKEN")
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")

# IDs de campos personalizados TID
ID_CAMPO_EPIC_LINK = "customfield_10001" 
ID_CAMPO_DOC_LINK = "customfield_22398"  
ID_CAMPO_TEST_SCOPE = "customfield_10163"    # end2end / system
ID_CAMPO_EXECUTION_MODE = "customfield_10150" # Manual / Automatic

jira_headers = {
    "Authorization": f"Bearer {JIRA_TOKEN}",
    "Accept": "application/json",
    "Content-Type": "application/json"
}

# --- PLANTILLA WIKI MARKUP ---
JIRA_WIKI_TEMPLATE = """h1. Test short description
----
{short_description}

h1. Pre-requisites
----
||ID||Pre-requisite||
{pre_requisites_rows}

h1. Test Data 
----
||ID||Test Data||
{test_data_rows}

h1. Steps & Expected Results
 (may reference an image or table to attach under this table)
----
||ID||Steps to Execute||Expected result||
{steps_rows}

h1. Notes and Special Considerations
----
||ID||Description||
{notes_rows}

h1. References (external to JIRA)
----
||ID||Description||Link||
{refs_rows}

{{panel:title=TIPS to create a good Test Plan}}
* Don't forget NON-FUNCTIONAL tests (performance, usability, interoperability, security)
* Don't forget cross-device tests
* Use an AI tool like Copilot for assistance to copy/paste with this template
* If you are a System Test Case (User Story that belongs to an Epic or Task), you must be linked to a User Story or Task (Test Case tests User Story/Task)
* If you are an E2E Test Case (Epic or individual User Story), you must be linked to an Epic or to the parent of the User Story (Test Case tests Epic/User Story)
{{panel}}
"""

def get_issue(issue_key):
    url = f"{JIRA_URL}/rest/api/2/issue/{issue_key}"
    res = requests.get(url, headers=jira_headers)
    return res.json() if res.status_code == 200 else None

def get_parent_epic_key(epic_data):
    links = epic_data['fields'].get('issuelinks', [])
    for link in links:
        l_type = link.get('type', {})
        if (link.get('inwardIssue') and l_type.get('inward') == 'is child of'):
            return link['inwardIssue']['key']
        if (link.get('outwardIssue') and l_type.get('outward') == 'is child of'):
            return link['outwardIssue']['key']
    return None

def create_test_case(project_key, summary, description, target_link_key, scope="system", mode="Manual", labels=None):
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
        print(f"Creado TC: {tc_key} ({mode}) vinculado a {target_link_key}")
        link_issues(target_link_key, tc_key)
        return tc_key
    return None

def link_issues(parent_key, tc_key):
    url = f"{JIRA_URL}/rest/api/2/issueLink"
    payload = {
        "type": {"name": "Test"}, 
        "inwardIssue": {"key": tc_key},
        "outwardIssue": {"key": parent_key}
    }
    requests.post(url, json=payload, headers=jira_headers)

def get_confluence_content(url):
    if not url or "confluence.tid.es" not in url: return ""
    page_id = re.search(r'pageId=(\d+)', url)
    page_id = page_id.group(1) if page_id else re.search(r'/view/(\d+)', url)
    page_id = page_id.group(1) if hasattr(page_id, 'group') else None
    if not page_id: return ""
    api_url = f"{CONFLUENCE_URL}/rest/api/content/{page_id}?expand=body.storage"
    res = requests.get(api_url, headers={"Authorization": f"Bearer {CONFLUENCE_TOKEN}"})
    return res.json().get('body', {}).get('storage', {}).get('value', "") if res.status_code == 200 else ""

def ask_copilot(prompt):
    url = "https://api.githubcopilot.com/chat/completions"
    headers = {"Authorization": f"Bearer {GITHUB_TOKEN}", "Content-Type": "application/json"}
    payload = {
        "model": "gpt-4",
        "messages": [
            {"role": "system", "content": "Eres un Ingeniero de QA Senior con experiencia en testing funcional, E2E, seguridad y validación de interfaces gráficas. Tu objetivo es diseñar un conjunto completo y estructurado de Test Cases basándote exclusivamente en la información proporcionada."},
            {"role": "user", "content": prompt}
        ],
        "temperature": 0.1
    }
    res = requests.post(url, json=payload, headers=headers)
    return res.json()['choices'][0]['message']['content'] if res.status_code == 200 else None

def main():
    try:
        payload = json.loads(os.getenv("JIRA_PAYLOAD", "{}"))
        us_key = payload.get("issue_key")
    except: return
    if not us_key: return

    us_data = get_issue(us_key)
    if not us_data: return
    project_key = us_data['fields']['project']['key']
    us_summary = us_data['fields']['summary']

    # Navegación a Épica Superior
    epic_key = us_data['fields'].get(ID_CAMPO_EPIC_LINK)
    parent_epic_key = us_key # Fallback
    if epic_key:
        epic_data = get_issue(epic_key)
        parent_epic_key = get_parent_epic_key(epic_data) or epic_key

    doc_url = get_issue(parent_epic_key)['fields'].get(ID_CAMPO_DOC_LINK) if parent_epic_key else None
    contexto = get_confluence_content(doc_url)

    prompt = f"""
    Eres un Ingeniero de QA Senior. Tu objetivo es diseñar un conjunto completo de Test Cases basándote en la información proporcionada.

    ### CONTEXTO
    US Summary: {us_summary}
    US Description: {us_data['fields']['description']}
    Technical Context (Confluence): {contexto[:2000]}

    ### INSTRUCCIONES GENERALES:
    1. Analiza la User Story desde múltiples perspectivas de calidad (Funcional, E2E, Seguridad, UI, Integración, Usabilidad, Errores y Regresión).
    2. NO inventes requisitos: si falta información, documenta la suposición como “Assumption”.
    3. Prioriza los casos según impacto y riesgo.
    4. Incluye escenarios positivos, negativos y edge cases.
    5. Usa lenguaje claro, preciso y orientado a ejecución manual y/o automatización.

    ### TAREA:
    Genera 3 escenarios críticos. Para cada uno:
    1. Identifica la función principal de la US y crea un título que empiece con dicha función entre corchetes: "[Nombre Funcion] Título del Test".
    2. Crea una descripción usando exactamente este esquema de tablas de Jira Wiki:
    h1. Test short description
    ----
    (Resumen corto del escenario y perspectiva aplicada)
    h1. Pre-requisites
    ----
    ||ID||Pre-requisite||
    |1|(Dato o Assumption inicial)|
    h1. Test Data
    ----
    ||ID||Test Data||
    |1|(Dato necesario para la ejecución)|
    h1. Steps & Expected Results
    ----
    ||ID||Steps to Execute||Expected result||
    |1|(Acción técnica)|(Resultado esperado)|
    h1. Notes and Special Considerations
    ----
    ||ID||Description||
    |1|(Notas sobre seguridad o UI)|
    h1. References (external to JIRA)
    ----
    ||ID||Description||Link||
    |1|(Ref. a Confluence)|(URL)|

    3. Genera el código Selenium Python correspondiente.
    4. Clasifica: 'E2E' (si cruza sistemas) o 'system' (funcional local).

    Responde SOLO JSON:
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
    
    print(f"Analizando {us_key} con perspectiva Senior QA...")
    respuesta = ask_copilot(prompt)
    
    if respuesta:
        try:
            clean_json = re.sub(r'```json|```', '', respuesta).strip()
            scenarios = json.loads(clean_json)
            for sc in scenarios:
                summary_base = f"[{sc['main_function']}] {sc['test_title']}"
                scope = sc.get('scope', 'system')
                
                # REGLA DE VINCULACIÓN: E2E -> Épica Superior | System -> User Story
                link_target = parent_epic_key if scope == "E2E" else us_key
                
                # Versión Manual
                create_test_case(project_key, f"{summary_base} - Manual", sc['formatted_description'] + "\n" + JIRA_WIKI_TEMPLATE.split("panel")[1], link_target, scope, "Manual", ["IA_manual"])
                
                # Versión Automática
                auto_desc = f"{sc['formatted_description']}\n\nh1. 5. Automation (Selenium)\n{{code:python}}\n{sc['automation_code']}\n{{code}}"
                create_test_case(project_key, f"{summary_base} - Automatic", auto_desc, link_target, scope, "Automatic", ["IA_automatico"])
        except Exception as e:
            print(f"Error: {e}")

if __name__ == "__main__":
    main()