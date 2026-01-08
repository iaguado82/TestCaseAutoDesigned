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
    parser = argparse.ArgumentParser(description="Generate Jira Test Cases from a Jira US using AI.")
    parser.add_argument("--issue", help="Jira issue key (e.g., MULTISTC-31379). If omitted, uses MANUAL_ISSUE_KEY env var.")
    args = parser.parse_args()

    issue_key = (args.issue or os.getenv("MANUAL_ISSUE_KEY", "")).strip()
    if not issue_key:
        print("ERROR: No se proporcionó issue. Usa --issue o define MANUAL_ISSUE_KEY.", flush=True)
        raise SystemExit(2)

    run_main(issue_key)


if __name__ == "__main__":
    main()
