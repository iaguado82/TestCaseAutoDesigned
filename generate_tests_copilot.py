import os
import requests
import json
import sys

# --- CONFIGURACIÓN BASADA ÚNICAMENTE EN VARIABLES DE ENTORNO ---
# Se eliminan los valores por defecto para asegurar el uso del fichero .env
JIRA_URL = os.getenv("JIRA_URL")
CONFLUENCE_URL = os.getenv("CONFLUENCE_URL")
JIRA_USER = os.getenv("JIRA_USERNAME")
JIRA_TOKEN = os.getenv("JIRA_PERSONAL_TOKEN")
CONFLUENCE_TOKEN = os.getenv("CONFLUENCE_PERSONAL_TOKEN")
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")

# Verificación de carga de variables críticas
if not all([JIRA_URL, CONFLUENCE_URL, JIRA_TOKEN, GITHUB_TOKEN]):
    print("Error: Faltan variables de entorno críticas en el fichero .env")
    sys.exit(1)

# Headers de autenticación para Jira (Personal Access Token)
jira_headers = {
    "Authorization": f"Bearer {JIRA_TOKEN}",
    "Accept": "application/json",
    "Content-Type": "application/json"
}

# Headers de autenticación para Confluence
confluence_headers = {
    "Authorization": f"Bearer {CONFLUENCE_TOKEN}",
    "Accept": "application/json",
    "Content-Type": "application/json"
}

def get_context_from_confluence(page_id):
    """Recupera el contenido de una página de la wiki de Confluence."""
    if not page_id:
        return "No se proporcionó ID de página de Confluence."
        
    url = f"{CONFLUENCE_URL.rstrip('/')}/rest/api/content/{page_id}?expand=body.storage"
    try:
        res = requests.get(url, headers=confluence_headers, verify=True)
        if res.status_code == 200:
            return res.json()['body']['storage']['value']
        else:
            print(f"Error al leer Confluence: {res.status_code}")
            return ""
    except Exception as e:
        print(f"Excepción en Confluence: {e}")
        return ""

def ask_copilot(prompt):
    """Consulta al modelo de Copilot Business mediante su API."""
    url = "https://api.githubcopilot.com/chat/completions"
    headers_copilot = {
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "Content-Type": "application/json"
    }
    payload = {
        "model": "gpt-4",
        "messages": [
            {
                "role": "system", 
                "content": "Eres un QA Automation Engineer experto de Telefónica. Generas casos de prueba técnicos basados en requerimientos."
            },
            {"role": "user", "content": prompt}
        ],
        "temperature": 0.1
    }
    
    try:
        response = requests.post(url, json=payload, headers=headers_copilot)
        if response.status_code == 200:
            return response.json()['choices'][0]['message']['content']
        else:
            print(f"Error en Copilot API: {response.status_code}")
            return None
    except Exception as e:
        print(f"Excepción en Copilot: {e}")
        return None

def create_and_link_test(us_key, tc_data, project_key):
    """Crea un nuevo ticket de tipo 'Test' y lo vincula a la User Story."""
    issue_url = f"{JIRA_URL.rstrip('/')}/rest/api/2/issue"
    payload = {
        "fields": {
            "project": {"key": project_key},
            "summary": f"[Auto-QA] {tc_data.get('titulo', 'Test Case')}",
            "description": tc_data.get('pasos', 'Sin pasos.'),
            "issuetype": {"name": "Test"}
        }
    }
    
    res = requests.post(issue_url, json=payload, headers=jira_headers)
    if res.status_code == 201:
        new_test_key = res.json()['key']
        # Enlace de incidencias
        link_url = f"{JIRA_URL.rstrip('/')}/rest/api/2/issueLink"
        link_payload = {
            "type": {"name": "Relates"},
            "inwardIssue": {"key": us_key},
            "outwardIssue": {"key": new_test_key}
        }
        requests.post(link_url, json=link_payload, headers=jira_headers)
        return new_test_key
    return None

def main():
    """Ejecución principal del flujo de automatización."""
    try:
        raw_payload = os.getenv("JIRA_PAYLOAD", "{}")
        input_data = json.loads(raw_payload)
        
        us_key = input_data.get("issue_key")
        us_summary = input_data.get("summary")
        us_description = input_data.get("description")
        project_key = input_data.get("project")
        page_id = input_data.get("confluence_page_id", "12345") 
        
    except Exception as e:
        print(f"Error al procesar payload: {e}")
        sys.exit(1)

    if not us_key:
        print("Esperando evento de Jira...")
        return

    context = get_context_from_confluence(page_id)
    
    prompt = f"""
    Genera 3 casos de prueba para: {us_summary}
    Descripción: {us_description}
    Contexto Técnico: {context}
    
    Responde solo en JSON: [{{"titulo": "...", "pasos": "..."}}]
    """
    
    raw_response = ask_copilot(prompt)
    if raw_response:
        clean_json = raw_response.replace("```json", "").replace("```", "").strip()
        test_cases = json.loads(clean_json)
        for tc in test_cases:
            create_and_link_test(us_key, tc, project_key)

if __name__ == "__main__":
    main()