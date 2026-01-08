def log_scenario_sources(sc, provenance):
    iid = sc.get("inventory_id")

    print("\n----------------------------------------", flush=True)
    print(f"INFO FUENTES – inventory_id={iid}", flush=True)

    print("- Fuente de verdad:", flush=True)
    for k in provenance.get("truth_issues", []):
        print(f"  * {k}", flush=True)

    if provenance.get("referenced_issues"):
        print("- Contexto adicional (Jira):", flush=True)
        for k in provenance["referenced_issues"]:
            print(f"  * {k}", flush=True)

    if provenance.get("confluence_urls"):
        print("- Contexto adicional (Confluence):", flush=True)
        for url in provenance["confluence_urls"]:
            print(f"  * {url}", flush=True)

    if provenance.get("anchor_epic"):
        print("- Anchor E2E:", flush=True)
        print(f"  * {provenance['anchor_epic']}", flush=True)

    print("----------------------------------------\n", flush=True)
