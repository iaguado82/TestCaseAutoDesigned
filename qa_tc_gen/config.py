import os

# Soporte para carga de variables de entorno locales
try:
    from dotenv import load_dotenv
    _env_loaded = load_dotenv()
except Exception:
    _env_loaded = False

from .utils_text import clean_token

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
ID_CAMPO_EPIC_LINK = os.getenv("ID_CAMPO_EPIC_LINK", "customfield_11600").strip()
ID_CAMPO_DOC_LINK = os.getenv("ID_CAMPO_DOC_LINK", "customfield_22398").strip()
ID_CAMPO_TEST_SCOPE = os.getenv("ID_CAMPO_TEST_SCOPE", "customfield_10163").strip()
ID_CAMPO_EXECUTION_MODE = os.getenv("ID_CAMPO_EXECUTION_MODE", "customfield_10150").strip()

# NUEVO: Automation Candidate
ID_CAMPO_AUTOMATION_CANDIDATE = os.getenv("ID_CAMPO_AUTOMATION_CANDIDATE", "customfield_10161").strip()
# valores: High | Low | Discarded

# --- Relación de links (multi-proyecto) ---
# Link types que identifican la "fuente de verdad" alternativa (dependency US)
# Se comparan contra type.name, type.inward y type.outward. Separador: |
DEPENDENCY_LINK_NAMES = [
    s.strip().lower()
    for s in os.getenv("DEPENDENCY_LINK_NAMES", "is a dependency for").split("|")
    if s.strip()
]

# Link types para subir a "padre" de una épica (anchor tipo JEFE-xxx)
PARENT_EPIC_LINK_NAMES = [
    s.strip().lower()
    for s in os.getenv("PARENT_EPIC_LINK_NAMES", "is child of").split("|")
    if s.strip()
]

jira_headers = {
    "Authorization": f"Bearer {JIRA_PERSONAL_TOKEN}",
    "Accept": "application/json",
    "Content-Type": "application/json"
}
