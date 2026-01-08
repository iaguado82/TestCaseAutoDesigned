import os
import sys
import argparse

# Forzar salida más fiable en consola (evita que se “pierdan” prints puntuales)
try:
    sys.stdout.reconfigure(line_buffering=True)
except Exception:
    pass

from qa_tc_gen.generator import run_main


def main():
    parser = argparse.ArgumentParser(
        description="Generate Jira Test Cases from a Jira US using AI."
    )
    parser.add_argument(
        "--issue",
        help="Jira issue key (e.g., MULTISTC-31379). If omitted, uses MANUAL_ISSUE_KEY env var.",
    )
    parser.add_argument(
        "--target-project",
        dest="target_project",
        help=(
            "Jira target project key where Test Cases will be created (e.g., MULTISTC). "
            "If omitted, uses TARGET_PROJECT env var (fallback: config.py TARGET_PROJECT)."
        ),
    )

    args = parser.parse_args()

    issue_key = (args.issue or os.getenv("MANUAL_ISSUE_KEY", "")).strip()
    if not issue_key:
        print("ERROR: No se proporcionó issue. Usa --issue o define MANUAL_ISSUE_KEY.", flush=True)
        raise SystemExit(2)

    # Opcional: si no llega por CLI, lo resuelve generator.py por env/config.
    target_project = (args.target_project or "").strip() or None

    # FAIL-FAST ORQUESTACIÓN:
    # - run_main devolverá un exit code (int) para automatización (CI/n8n/cron)
    # - 0: OK
    # - 10: cuota diaria rate-limit
    # - 11: reintentos agotados / rate-limit temporal no resuelto
    # - 12: request too large (413)
    # - 20: validación/cobertura fallida (no crea TCs)
    # - 30: error Jira/IO no recuperable
    try:
        exit_code = run_main(issue_key, target_project=target_project)
    except Exception as e:
        print(f"ERROR: Fallo no controlado en ejecución: {e}", flush=True)
        raise SystemExit(99)

    # Si generator aún no devuelve nada (None), tratamos como error genérico
    if exit_code is None:
        exit_code = 1

    raise SystemExit(int(exit_code))


if __name__ == "__main__":
    main()
