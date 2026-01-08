import re
import requests

from .config import CONFLUENCE_URL, CONFLUENCE_PERSONAL_TOKEN
from .utils_text import strip_html_tags


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
