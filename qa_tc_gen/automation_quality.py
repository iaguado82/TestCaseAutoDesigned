def is_true(v):
    """Normaliza truthy para posibles variantes del modelo."""
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return v != 0
    if isinstance(v, str):
        return v.strip().lower() in ["true", "yes", "1", "si", "sí"]
    return False


def looks_like_placeholder(code: str) -> bool:
    c = (code or "").strip().lower()
    if not c:
        return True
    bad_tokens = [
        "todo", "...", "placeholder", "selenium_code_for_", "appium_code_for_",
        "lorem", "tbd", "por completar"
    ]
    return any(t in c for t in bad_tokens)


def looks_like_fake_endpoint_or_auth(code: str) -> bool:
    """
    Heurística: descarta snippets con dominios de ejemplo o auth fake.
    """
    c = (code or "").lower()
    bad = [
        "example.com", "http://example", "https://example",
        "bearer token", "your_token", "insert_token", "changeme",
        "mib.example", "api.example", "testapplication.com"
    ]
    return any(b in c for b in bad)


def is_mutating_api_code(code: str) -> bool:
    """
    Heurística: descarta automatizaciones API que mutan estado (MiB/config/backoffice)
    salvo que tengáis harness real (no lo asumimos).
    """
    low = (code or "").lower()
    mutating_markers = [
        "requests.post", "requests.put", "requests.patch", "requests.delete",
        ".post(", ".put(", ".patch(", ".delete("
    ]
    return any(m in low for m in mutating_markers)


def mentions_backoffice_or_config(sc) -> bool:
    """
    Heurística textual para detectar pruebas de configuración/backoffice.
    """
    t = (sc.get("test_title") or "").lower()
    d = (sc.get("formatted_description") or "").lower()
    k = " ".join([t, d])
    tokens = [
        "mib", "backoffice", "cms", "configur", "configuración", "parametr",
        "feature flag", "toggle", "habilitar", "deshabilitar"
    ]
    return any(tok in k for tok in tokens)


def is_quality_automation(auto_type: str, code: str, sc=None) -> bool:
    """
    Quality gate mínimo para evitar marcar High/Low con snippets/placeholder.
    Si no pasa, se marca Discarded.
    """
    c = (code or "").strip()
    t = (auto_type or "none").strip().lower()
    sc = sc or {}

    if t not in ["selenium", "appium", "api"]:
        return False
    if looks_like_placeholder(c):
        return False
    if looks_like_fake_endpoint_or_auth(c):
        return False

    # Nueva regla: si huele a backoffice/config, por defecto Discarded
    if mentions_backoffice_or_config(sc):
        return False

    # API: no permitimos mutaciones (POST/PUT/PATCH/DELETE) como candidate por defecto
    if t == "api" and is_mutating_api_code(c):
        return False

    # Umbral mínimo de contenido (evita 1-liners)
    if len(c) < 600:
        return False

    low = c.lower()

    if t in ["selenium", "appium"]:
        # Debe tener esperas y asserts/checks
        required_any = ["webdriverwait", "expected_conditions", "wait.until"]
        if not any(r in low for r in required_any):
            return False
        if "assert" not in low and "expect" not in low:
            return False

        # Si el test exige equivalencia ("igual", "misma"), exige assert comparativo
        td = (sc.get("formatted_description") or "").lower()
        if ("misma" in td or "igual" in td) and "==" not in low:
            return False

    if t == "api":
        if "assert" not in low and "expect" not in low:
            return False

    return True


def compute_automation_label(sc) -> str:
    """
    Mapea a valores del customfield Automation Candidate:
    - High / Low / Discarded

    Política (más conservadora, para evitar “candidatas complejas”):
    - Discarded si automation_candidate=false o no pasa quality gate.
    - High si selenium/appium y pasa quality gate y NO es config/backoffice.
    - Low  si api (solo lectura) y pasa quality gate.
    """
    candidate = is_true(sc.get("automation_candidate", False))
    auto_type = (sc.get("automation_type", "") or "none").strip().lower()
    code = sc.get("automation_code", "") or ""

    if not candidate:
        return "Discarded"
    if not is_quality_automation(auto_type, code, sc=sc):
        return "Discarded"

    if auto_type in ["selenium", "appium"]:
        return "High"
    if auto_type == "api":
        return "Low"
    return "Discarded"


def append_automation_block_to_description(manual_desc: str, sc) -> str:
    """
    Añade al final del description manual un bloque con la propuesta de automatización,
    manteniendo lo manual siempre.
    Solo añade bloque si el Automation Candidate no es Discarded.
    """
    base = manual_desc or ""
    label = compute_automation_label(sc)
    if label == "Discarded":
        return base

    auto_type = (sc.get("automation_type", "") or "none").strip().lower()
    code = (sc.get("automation_code", "") or "").strip()

    if not code:
        return base

    block = (
        "\n\n"
        "h1. Automatización (Propuesta)\n"
        "----\n"
        f"* Automation Candidate: {label}\n"
        f"* Tipo recomendado: {auto_type}\n"
        "* Nota: Este bloque es informativo. El caso de prueba manual sigue siendo la referencia ejecutable.\n\n"
        "{code:python}\n"
        f"{code}\n"
        "{code}\n"
    )
    return base + block


def append_kpi_block_option_a(manual_desc: str, sc) -> str:
    """
    Opción A: añadir KPIs en la descripción (sin cambiar el JSON).
    Añade bloque SOLO si el escenario trata de transición/carga/animación/tiempos,
    o si es E2E y menciona UI/UX (carrusel, navegación, preview, detalle, animaciones).
    """
    base = manual_desc or ""
    title = (sc.get("test_title") or "").lower()
    mf = (sc.get("main_function") or "").lower()
    body = (sc.get("formatted_description") or "").lower()
    txt = " ".join([title, mf, body])

    kpi_tokens = [
        "carga", "cargar", "tiempo", "latencia", "transición", "transicion",
        "animación", "animacion", "render", "pint", "apertura", "abrir",
        "detalle", "home", "preview", "foco", "navegación", "navegacion",
        "scroll", "stutter", "frames", "jank", "progreso", "progress"
    ]
    is_uiish = any(t in txt for t in kpi_tokens)
    if not is_uiish:
        return base

    block = (
        "\n\n"
        "h1. KPI / Rendimiento (si aplica)\n"
        "----\n"
        "* Objetivo: medir tiempos percibidos por usuario y detectar degradaciones.\n"
        "* Método recomendado (prioridad): Logs/telemetría de app con timestamps (evento *_start / *_ready).\n"
        "* Alternativa: medición por driver (t0 al input; t1 cuando pantalla/estado 'ready' sea observable).\n"
        "* Ejecución: 5 repeticiones mínimo; reportar p50 y p95.\n"
        "* Criterio de aceptación: si no hay SLA, usar baseline del release anterior y alertar si p95 empeora >20%.\n"
    )

    metrics = []
    if "detalle" in txt or "info" in txt or "opc" in txt or "ficha" in txt:
        metrics.append("* KPI: Time-to-Detail (TTD) | Start: pulsación INFO/OPC/OK | End: detalle 'ready' (contenido+UI estable).")
    if "carrusel" in txt or "home" in txt:
        metrics.append("* KPI: Time-to-Carousel-Interactive (TCI) | Start: entrada en Home | End: carrusel visible + foco navegable.")
    if "preview" in txt or "imagen" in txt or "portrait" in txt or "landscape" in txt:
        metrics.append("* KPI: Time-to-Preview (TTP) | Start: foco en item | End: preview renderizada (imagen visible).")
    if "anim" in txt or "scroll" in txt or "naveg" in txt:
        metrics.append("* KPI: Jank/Fluidez | Medida: frames drops durante navegación horizontal; si no hay métrica, registrar stutter en logs.")

    if metrics:
        block += "\n" + "\n".join(metrics) + "\n"

    return base + block
