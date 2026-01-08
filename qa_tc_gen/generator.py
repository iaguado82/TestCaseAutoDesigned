# generator.py
from .utils_text import strip_html_tags

from .context_builder import (
    resolve_target_project,
    build_truth_sources,
    resolve_anchor_epic,
    build_additional_context,
    build_epic_context,
    build_truth_text,
    build_provenance,
)
from .llm_budget import build_llm_payload
from .scenario_engine import generate_scenarios_with_full_coverage
from .publisher import publish_test_cases


# ---------------------------
# Exit codes (fail-fast / CI-friendly)
# ---------------------------
EXIT_OK = 0
EXIT_RATE_LIMIT_DAILY = 10
EXIT_RATE_LIMIT_RETRIES_EXHAUSTED = 11
EXIT_REQUEST_TOO_LARGE = 12
EXIT_COVERAGE_VALIDATION_FAILED = 20
EXIT_JIRA_OR_IO_ERROR = 30
EXIT_UNKNOWN_ERROR = 99


def run_main(us_key: str, target_project: str | None = None):
    """
    Entry point.
    - us_key: Issue key de entrada (US).
    - target_project: Proyecto Jira donde se crean los Test Cases (si None, se resuelve por env/config).

    Devuelve exit code (int) para automatización.
    """
    print("--- DIAGNÓSTICO DE INICIO ---", flush=True)

    if not us_key:
        print("ERROR: MANUAL_ISSUE_KEY no detectada.", flush=True)
        return EXIT_JIRA_OR_IO_ERROR

    target_project_key = resolve_target_project(target_project)
    if not target_project_key:
        print("ERROR: TARGET_PROJECT no definido (ni --target-project ni env ni config).", flush=True)
        return EXIT_JIRA_OR_IO_ERROR

    print(f"--- Procesando: {us_key} ---", flush=True)
    print(f"INFO: Target project para Test Cases: {target_project_key}", flush=True)

    truth_pack = build_truth_sources(us_key)
    us_data = truth_pack.get("us_data")
    if not us_data:
        return EXIT_JIRA_OR_IO_ERROR

    us_summary = us_data["fields"].get("summary", "")
    us_description_raw = us_data["fields"].get("description", "") or ""
    _ = strip_html_tags(us_description_raw)

    print(f"Resumen US:\n\n{us_summary}", flush=True)

    truth_issues = truth_pack["truth_issues"]
    truth_issue_keys = truth_pack["truth_issue_keys"]
    dependency_keys = truth_pack.get("dependency_keys") or []
    if dependency_keys:
        print(f"INFO: Detectadas dependencias (truth candidates): {dependency_keys}", flush=True)

    parent_epic_key = resolve_anchor_epic(us_key, us_data)
    print(f"INFO: Jerarquía vinculación E2E -> {parent_epic_key}", flush=True)

    extra_ctx_pack = build_additional_context(truth_issues, truth_issue_keys)
    extra_context = extra_ctx_pack["extra_context"]
    referenced_issue_keys_used = extra_ctx_pack["referenced_issue_keys_used"]
    confluence_urls_used = extra_ctx_pack["confluence_urls_used"]

    epic_ctx_pack = build_epic_context(parent_epic_key)
    contexto_epica = epic_ctx_pack["contexto_epica"]
    doc_url = epic_ctx_pack["doc_url"]
    if doc_url and contexto_epica:
        confluence_urls_used.append(doc_url)

    context_provenance = build_provenance(
        truth_issue_keys=truth_issue_keys,
        referenced_issue_keys_used=referenced_issue_keys_used,
        confluence_urls_used=list(dict.fromkeys(confluence_urls_used)),
        anchor_epic=parent_epic_key,
    )

    print("\n" + "=" * 50, flush=True)
    print("TRAZABILIDAD GLOBAL DE CONTEXTO (TRUTH + CONTEXTO ADICIONAL):", flush=True)
    print(f"- Truth issues (seed+linked): {context_provenance['truth_issues']}", flush=True)
    print(f"- Referenced issues usados (context): {context_provenance['referenced_issues']}", flush=True)
    print(f"- Confluence usado (context): {context_provenance['confluence_urls']}", flush=True)
    print(f"- Anchor E2E: {context_provenance['anchor_epic']}", flush=True)
    print("=" * 50 + "\n", flush=True)

    truth_text = build_truth_text(truth_issues)

    payload = build_llm_payload(
        truth_text=truth_text,
        context_text=extra_context,
        confluence_text=contexto_epica,
    )

    ok, msg, result = generate_scenarios_with_full_coverage(us_key=us_key, payload=payload)
    if not ok:
        print(f"ERROR: {msg}", flush=True)

        # Mensajes concretos según causa
        if "TOTAL_INVENTARIO" in msg:
            return EXIT_UNKNOWN_ERROR
        if "Validación de cobertura fallida" in msg:
            print("INFO: No se crearán Test Cases en Jira para evitar cobertura parcial.", flush=True)
            return EXIT_COVERAGE_VALIDATION_FAILED

        return EXIT_RATE_LIMIT_RETRIES_EXHAUSTED

    scenarios = result["scenarios"]
    n_total = result["n_total"]

    print(f"INFO: Cobertura OK. Procesando {len(scenarios)} escenarios generados...", flush=True)

    created = publish_test_cases(
        us_key=us_key,
        target_project_key=target_project_key,
        parent_epic_key=parent_epic_key,
        scenarios=scenarios,
        context_provenance=context_provenance,
    )

    print(f"INFO: Test Cases creados: {created} (TOTAL_INVENTARIO={n_total})", flush=True)
    print(f"--- Proceso finalizado para {us_key} ---", flush=True)
    return EXIT_OK
