import requests
from .config import (
    JIRA_URL, jira_headers,
    ID_CAMPO_EPIC_LINK, ID_CAMPO_DOC_LINK,
    ID_CAMPO_TEST_SCOPE, ID_CAMPO_EXECUTION_MODE,
    ID_CAMPO_AUTOMATION_CANDIDATE,
    DEPENDENCY_LINK_NAMES,
    PARENT_EPIC_LINK_NAMES,
)


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


def _link_type_matches(link_type: dict, accepted_names_lower: list[str]) -> bool:
    """
    Comprueba match contra type.name / type.inward / type.outward.
    """
    if not link_type or not accepted_names_lower:
        return False
    candidates = [
        (link_type.get("name") or "").strip().lower(),
        (link_type.get("inward") or "").strip().lower(),
        (link_type.get("outward") or "").strip().lower(),
    ]
    return any(c and c in accepted_names_lower for c in candidates)


def get_linked_issue_keys_by_link_names(issue_data, accepted_link_names_lower: list[str]) -> list[str]:
    """
    Devuelve claves de issues enlazados por issuelinks cuyo type coincide con accepted_link_names_lower.
    Sirve tanto para dependencias como para relaciones padre/hijo.

    Nota: devuelve BOTH sides (inwardIssue y outwardIssue) cuando existan.
    """
    if not issue_data:
        return []

    fields = issue_data.get("fields", {}) or {}
    links = fields.get("issuelinks", []) or []

    out = []
    for link in links:
        l_type = link.get("type", {}) or {}
        if not _link_type_matches(l_type, accepted_link_names_lower):
            continue

        inward = link.get("inwardIssue", {})
        outward = link.get("outwardIssue", {})

        if inward and inward.get("key"):
            out.append(inward["key"])
        if outward and outward.get("key"):
            out.append(outward["key"])

    # dedupe preservando orden
    deduped = list(dict.fromkeys(out))
    return deduped


def get_dependency_issue_keys(issue_data) -> list[str]:
    """
    Issues cuya relación sea "is a dependency for" (o lo que definas en DEPENDENCY_LINK_NAMES).
    Estos se consideran candidatos a "fuente de verdad" alternativa.
    """
    return get_linked_issue_keys_by_link_names(issue_data, DEPENDENCY_LINK_NAMES)


def get_parent_epic_key(epic_data):
    """
    Obtiene la épica padre a través de issuelinks, usando nombres configurables.
    (por defecto: 'is child of')
    """
    keys = get_linked_issue_keys_by_link_names(epic_data, PARENT_EPIC_LINK_NAMES)
    # Si hay varios, coge el primero (habitual en jerarquías)
    return keys[0] if keys else None


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


def create_test_case(project_key, summary, description, target_link_key,
                     scope="System", mode="Manual", labels=None, automation_candidate_value="Discarded"):
    """
    Crea un nuevo Test Case en Jira.
    - SIEMPRE crea el TC en modo Manual (mode=Manual), pero rellena customfield Automation Candidate
      con High/Low/Discarded.
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
        print(
            f"Éxito: TC {tc_key} ({mode_value}/{scope_value}) creado. AutomationCandidate={automation_candidate_value}",
            flush=True
        )
        link_issues(target_link_key, tc_key)
        return tc_key

    print(f"Error creando TC {mode_value}: {res.status_code} - {res.text}", flush=True)
    return None


def get_epic_link_key(issue_data):
    return (issue_data.get("fields", {}) or {}).get(ID_CAMPO_EPIC_LINK)


def get_doc_link(issue_data):
    return (issue_data.get("fields", {}) or {}).get(ID_CAMPO_DOC_LINK)
