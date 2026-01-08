# context_builder.py
import os
import re
from typing import Dict, Any, List, Tuple

# Fallback (legacy) si no llega por CLI ni env
from .config import TARGET_PROJECT as DEFAULT_TARGET_PROJECT

from .utils_text import strip_html_tags
from .jira_client import (
    get_issue,
    get_parent_epic_key,
    get_doc_link,
    get_epic_link_key,
    get_dependency_issue_keys,
)
from .confluence_client import get_confluence_content
from .llm_budget import clip_text


# ---------------------------
# Presupuestos / límites (no LLM)
# ---------------------------
MAX_TRUTH_BLOCK_CHARS_PER_ISSUE = 8000
MAX_REFERENCED_JIRA_TICKETS = 8
MAX_REFERENCED_JIRA_DESC_CHARS = 900
MAX_EPIC_CONFLUENCE_CHARS = 2500
CONTEXT_REFERENCE_DEPTH = 1
MAX_TRUTH_ISSUES = 10


# ---------------------------
# Regex utilidades
# ---------------------------
JIRA_KEY_RE = re.compile(r"([A-Z][A-Z0-9]+-\d+)")
CONF_URL_RE = re.compile(r"https?://confluence\.tid\.es/[^\s\]\)\|\,\>\"\' ]+")


def extract_jira_keys_and_conf_urls(text: str) -> Tuple[List[str], List[str]]:
    if not text:
        return [], []
    keys = list(dict.fromkeys(JIRA_KEY_RE.findall(text)))
    conf_urls = list(dict.fromkeys(CONF_URL_RE.findall(text)))
    return keys, conf_urls


def resolve_target_project(cli_target_project: str | None = None) -> str:
    """
    Orden de precedencia:
    1) CLI (--target-project)
    2) ENV TARGET_PROJECT
    3) DEFAULT_TARGET_PROJECT (config.py)
    """
    if cli_target_project and str(cli_target_project).strip():
        return str(cli_target_project).strip()
    env_tp = (os.getenv("TARGET_PROJECT", "") or "").strip()
    if env_tp:
        return env_tp
    return (DEFAULT_TARGET_PROJECT or "").strip()


def build_truth_sources(us_key: str) -> Dict[str, Any]:
    """
    Devuelve:
    - us_data
    - truth_issues (lista con key/summary/description/description_raw)
    - truth_issue_keys
    """
    us_data = get_issue(us_key)
    if not us_data:
        return {"us_data": None, "truth_issues": [], "truth_issue_keys": []}

    dependency_keys = get_dependency_issue_keys(us_data)
    truth_seed_keys = [us_key] + [k for k in dependency_keys if k != us_key]
    truth_seed_keys = list(dict.fromkeys(truth_seed_keys))

    truth_linked_keys: List[str] = []
    for seed_key in truth_seed_keys:
        seed_data = us_data if seed_key == us_key else get_issue(seed_key)
        if not seed_data:
            continue
        seed_desc_raw = (seed_data.get("fields", {}) or {}).get("description", "") or ""
        keys_in_desc, _ = extract_jira_keys_and_conf_urls(seed_desc_raw)
        for k in keys_in_desc:
            if k not in truth_seed_keys and k not in truth_linked_keys and k != us_key:
                truth_linked_keys.append(k)

    truth_issue_keys = list(dict.fromkeys(truth_seed_keys + truth_linked_keys))

    if len(truth_issue_keys) > MAX_TRUTH_ISSUES:
        truth_issue_keys = truth_issue_keys[:MAX_TRUTH_ISSUES]

    truth_issues = []
    for key in truth_issue_keys:
        data = us_data if key == us_key else get_issue(key)
        if not data:
            continue
        summary = data.get("fields", {}).get("summary", "") or ""
        desc_raw = data.get("fields", {}).get("description", "") or ""
        desc = strip_html_tags(desc_raw)
        desc = clip_text(f"truth:{key}", desc, MAX_TRUTH_BLOCK_CHARS_PER_ISSUE)

        truth_issues.append(
            {"key": key, "summary": summary, "description": desc, "description_raw": desc_raw}
        )

    return {
        "us_data": us_data,
        "truth_issues": truth_issues,
        "truth_issue_keys": truth_issue_keys,
        "dependency_keys": dependency_keys,
    }


def resolve_anchor_epic(us_key: str, us_data: Dict[str, Any]) -> str:
    """
    Determina el anchor de E2E (épica/parent) manteniendo tu lógica.
    """
    epic_key = get_epic_link_key(us_data)
    parent_epic_key = us_key

    if epic_key:
        epic_data = get_issue(epic_key)
        if epic_data:
            possible_parent = get_parent_epic_key(epic_data)
            parent_epic_key = possible_parent if possible_parent else epic_key

    return parent_epic_key


def build_additional_context(truth_issues: List[Dict[str, Any]], truth_issue_keys: List[str]) -> Dict[str, Any]:
    """
    Construye:
    - extra_context
    - confluence_urls_used
    - referenced_issue_keys_used
    """
    extra_context = ""
    visited_issue_keys = set(truth_issue_keys)

    referenced_issue_keys_used: List[str] = []
    confluence_urls_used: List[str] = []

    def add_context_block(header: str, body: str):
        nonlocal extra_context
        if not body:
            return
        extra_context += f"\n{header}:\n{body}\n"

    conf_urls_all: List[str] = []
    referenced_keys_seed: List[str] = []

    for t in truth_issues:
        keys, conf_urls = extract_jira_keys_and_conf_urls(t.get("description_raw") or "")
        referenced_keys_seed.extend([k for k in keys if k not in truth_issue_keys])
        conf_urls_all.extend(conf_urls)

    conf_urls_all = list(dict.fromkeys(conf_urls_all))
    referenced_keys_seed = list(dict.fromkeys(referenced_keys_seed))

    # Confluence embebido en TRUTH
    for url in conf_urls_all:
        content = get_confluence_content(url) or ""
        if content:
            confluence_urls_used.append(url)
            add_context_block(f"DOCUMENTO CONFLUENCE {url}", clip_text("confluence", content, 1200))

    queue = [(k, 1) for k in referenced_keys_seed]
    extracted_count = 0

    while queue and extracted_count < MAX_REFERENCED_JIRA_TICKETS:
        key, depth = queue.pop(0)
        if key in visited_issue_keys:
            continue

        issue_data = get_issue(key)
        if not issue_data:
            visited_issue_keys.add(key)
            continue

        summary = issue_data.get("fields", {}).get("summary", "") or ""
        desc_raw = issue_data.get("fields", {}).get("description", "") or ""
        desc = strip_html_tags(desc_raw) or ""
        desc = clip_text(f"ref:{key}", desc, MAX_REFERENCED_JIRA_DESC_CHARS)

        referenced_issue_keys_used.append(key)
        add_context_block(f"INFO TICKET REFERENCIADO {key} | {summary}", desc)

        visited_issue_keys.add(key)
        extracted_count += 1

        if depth > 0 and CONTEXT_REFERENCE_DEPTH > 0:
            keys2, conf2 = extract_jira_keys_and_conf_urls(desc_raw)
            for u in conf2:
                if u not in conf_urls_all:
                    conf_urls_all.append(u)
            for k2 in keys2:
                if k2 not in visited_issue_keys and k2 not in truth_issue_keys:
                    queue.append((k2, depth - 1))

    return {
        "extra_context": extra_context,
        "confluence_urls_used": list(dict.fromkeys(confluence_urls_used)),
        "referenced_issue_keys_used": list(dict.fromkeys(referenced_issue_keys_used)),
    }


def build_epic_context(parent_epic_key: str) -> Dict[str, Any]:
    """
    Devuelve:
    - contexto_epica (texto)
    - doc_url (si aplica)
    """
    contexto_epica = ""
    doc_url = None

    parent_data = get_issue(parent_epic_key) if parent_epic_key else None
    doc_url = get_doc_link(parent_data) if parent_data else None
    if doc_url:
        contexto_epica = get_confluence_content(doc_url) or ""
        contexto_epica = clip_text("epic_confluence", contexto_epica, MAX_EPIC_CONFLUENCE_CHARS)

    return {"contexto_epica": contexto_epica, "doc_url": doc_url}


def build_truth_text(truth_issues: List[Dict[str, Any]]) -> str:
    truth_blocks = []
    for t in truth_issues:
        truth_blocks.append(
            f"Ticket: {t['key']}\n"
            f"Resumen: {t['summary']}\n"
            f"Descripción:\n{t['description']}\n"
        )
    return "\n\n---\n\n".join(truth_blocks)


def build_provenance(
    truth_issue_keys: List[str],
    referenced_issue_keys_used: List[str],
    confluence_urls_used: List[str],
    anchor_epic: str,
) -> Dict[str, Any]:
    return {
        "truth_issues": truth_issue_keys,
        "referenced_issues": referenced_issue_keys_used,
        "confluence_urls": confluence_urls_used,
        "anchor_epic": anchor_epic,
    }
