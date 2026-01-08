# publisher.py
from typing import Dict, Any, List

from .automation_quality import (
    compute_automation_label,
    append_automation_block_to_description,
)
from .jira_client import (
    create_test_case,
    link_issues,
)
from .utils_postprocess import to_corporate_template
from .utils_logging import log_scenario_sources
from .utils_text import normalize_jira_wiki



def publish_test_cases(
    us_key: str,
    target_project_key: str,
    parent_epic_key: str,
    scenarios: List[Dict[str, Any]],
    context_provenance: Dict[str, Any],
) -> int:
    """
    Crea Test Cases en Jira y devuelve cuántos se crearon (o intentaron).
    """
    scenarios_sorted = sorted(scenarios, key=lambda x: int(x.get("inventory_id", 0)))
    created_count = 0

    for sc in scenarios_sorted:
        title = sc.get("test_title", "Test")
        scope = sc.get("scope", "System")
        is_e2e = scope.lower() in ["e2e", "end2end"]

        link_target_primary = parent_epic_key if is_e2e else us_key

        summary_base = f"[{sc.get('main_function', 'QA')}] {title}"
        print(f"--- Creando: {summary_base} ({scope}) ---", flush=True)

        print(
            f"DEBUG AUTO inventory_id={sc.get('inventory_id')} "
            f"cand={sc.get('automation_candidate')} type={sc.get('automation_type')} "
            f"code_len={len((sc.get('automation_code') or '').strip())}",
            flush=True,
        )

        automation_candidate_value = compute_automation_label(sc)

        manual_desc = normalize_jira_wiki(sc.get("formatted_description", "") or "")
        final_manual = to_corporate_template(manual_desc)
        final_desc = append_automation_block_to_description(final_manual, sc)

        log_scenario_sources(sc, context_provenance)

        tc_key = create_test_case(
            target_project_key,
            f"{summary_base} - Manual",
            final_desc,
            link_target_primary,
            scope,
            "Manual",
            automation_candidate_value=automation_candidate_value,
        )

        if tc_key:
            created_count += 1

        if tc_key and is_e2e:
            if us_key and link_target_primary != us_key:
                link_issues(us_key, tc_key)

    return created_count
