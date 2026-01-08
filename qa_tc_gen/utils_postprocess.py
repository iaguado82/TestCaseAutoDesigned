import re

# Secciones "corporate" que queremos en tabla (las demás se preservan al final)
SECTION_MAP = {
    "breve descripción del test": "h1. Test short description",
    "test short description": "h1. Test short description",

    "pre-requisitos": "h1. Pre-requisites",
    "pre-requisites": "h1. Pre-requisites",

    "datos de prueba": "h1. Test Data",
    "test data": "h1. Test Data",

    "pasos y resultados esperados": "h1. Steps & Expected Results",
    "steps & expected results": "h1. Steps & Expected Results",

    "notas y consideraciones especiales": "h1. Notes and Special Considerations",
    "notes and special considerations": "h1. Notes and Special Considerations",

    # Si por error aparece, la descartamos (no la queremos)
    "references (external to jira)": None,
    "referencias (externas a jira)": None,
}

# Normalización de título h1
_H1_RE = re.compile(r"(?m)^\s*h1\.\s*(.+?)\s*$")
_SEPARATOR_RE = re.compile(r"(?m)^\s*----\s*$")

# Pasos estilo lista "# Acción: ... | Esperado: ..."
_STEP_LINE_RE = re.compile(r"(?m)^\s*#\s*Acción:\s*(.*?)\s*\|\s*Esperado:\s*(.*?)\s*$", re.IGNORECASE)

# Bullets "* ..." para convertir a tabla simple con ID
_BULLET_RE = re.compile(r"(?m)^\s*\*\s+(.*)$")


def _normalize_key(title: str) -> str:
    t = (title or "").strip().lower()
    t = re.sub(r"\s+", " ", t)
    return t


def _split_sections(text: str):
    """
    Devuelve lista de (raw_title, body_text) en el orden de aparición.
    body_text NO incluye el h1. ni el separador ---- (si existe).
    """
    if not text:
        return []

    s = text.replace("\r\n", "\n").replace("\r", "\n").strip()

    # Encuentra títulos h1 con sus posiciones
    matches = list(_H1_RE.finditer(s))
    if not matches:
        # Sin h1 -> todo como "short description"
        return [("Test short description", s)]

    sections = []
    for i, m in enumerate(matches):
        title = m.group(1).strip()
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(s)
        body = s[start:end].strip()

        # Si hay "----" al principio, quítalo
        body = _SEPARATOR_RE.sub("", body, count=1).strip()
        sections.append((title, body))

    return sections


def _table_two_cols(title_h1: str, header_right: str, items: list[str]) -> str:
    """
    Tabla: ||ID||<header_right>|| + filas |1|...|
    """
    out = [title_h1, "----", f"||ID||{header_right}||"]
    for idx, it in enumerate(items, start=1):
        it = (it or "").strip()
        if not it:
            continue
        out.append(f"|{idx}|{it}|")
    # Si no hay items, dejamos una fila vacía “1”
    if len(out) == 3:
        out.append("|1|Description|")
    return "\n".join(out).strip()


def _table_steps(title_h1: str, steps: list[tuple[str, str]]) -> str:
    """
    Tabla: ||ID||Steps to Execute||Expected result|| + filas
    """
    out = [
        title_h1,
        "(may reference an image or table to attach under this table)",
        "----",
        "||ID||Steps to Execute||Expected result||",
    ]
    for idx, (a, e) in enumerate(steps, start=1):
        a = (a or "").strip()
        e = (e or "").strip()
        if not a and not e:
            continue
        out.append(f"|{idx}|{a or 'Description'}|{e or 'Result'}|")
    if len(out) == 4:
        out.append("|1|Description|Result|")
    return "\n".join(out).strip()


def _extract_bullets(body: str) -> list[str]:
    return [m.group(1).strip() for m in _BULLET_RE.finditer(body or "") if m.group(1).strip()]


def _extract_steps(body: str) -> list[tuple[str, str]]:
    steps = []
    for m in _STEP_LINE_RE.finditer(body or ""):
        steps.append((m.group(1).strip(), m.group(2).strip()))
    return steps


def to_corporate_template(desc: str) -> str:
    """
    Convierte a plantilla corporativa con tablas y PRESERVA secciones extra al final.
    - Convierte: Short description, Pre-req, Test Data, Steps, Notes
    - Descarta: References (external to JIRA)
    - Preserva: KPI / Rendimiento, Automatización, etc. al final (tal cual, en texto).
    """
    if not desc:
        return ""

    sections = _split_sections(desc)

    # Buckets
    short_text = ""
    pre_items = []
    data_items = []
    steps_pairs = []
    notes_items = []
    extras = []  # (h1_title, body) que no mapeamos o queremos preservar

    for raw_title, body in sections:
        key = _normalize_key(raw_title)
        mapped = SECTION_MAP.get(key, "__EXTRA__")

        # References -> discard
        if mapped is None:
            continue

        if mapped == "h1. Test short description":
            # dejamos 1-2 líneas; si hay bullets, los juntamos
            bullets = _extract_bullets(body)
            if bullets:
                short_text = " ".join(bullets).strip()
            else:
                short_text = (body or "").strip()
            if not short_text:
                short_text = "Description"

        elif mapped == "h1. Pre-requisites":
            items = _extract_bullets(body)
            # si no hay bullets, intentamos separar por líneas no vacías
            if not items:
                items = [ln.strip() for ln in (body or "").split("\n") if ln.strip()]
            pre_items.extend(items)

        elif mapped == "h1. Test Data":
            items = _extract_bullets(body)
            if not items:
                items = [ln.strip() for ln in (body or "").split("\n") if ln.strip()]
            data_items.extend(items)

        elif mapped == "h1. Steps & Expected Results":
            steps = _extract_steps(body)
            # Si no hay formato Acción/Esperado, preservamos el body como "extra"
            if steps:
                steps_pairs.extend(steps)
            else:
                extras.append(("h1. Steps & Expected Results (raw)", body))

        elif mapped == "h1. Notes and Special Considerations":
            items = _extract_bullets(body)
            if not items:
                items = [ln.strip() for ln in (body or "").split("\n") if ln.strip()]
            notes_items.extend(items)

        else:
            # Sección no mapeada: preservamos tal cual (h1 + ---- + body)
            extras.append((raw_title, body))

    # Dedupe básico preservando orden
    def _dedupe(seq):
        return list(dict.fromkeys([x for x in seq if (x or "").strip()]))

    pre_items = _dedupe(pre_items)
    data_items = _dedupe(data_items)
    notes_items = _dedupe(notes_items)

    # Construcción final (corporate)
    parts = []

    parts.append("h1. Test short description")
    parts.append("----")
    parts.append(short_text.strip() if short_text else "Description")

    parts.append("")  # separación

    parts.append(_table_two_cols("h1. Pre-requisites", "Pre-requisite", pre_items))
    parts.append("")

    parts.append(_table_two_cols("h1. Test Data", "Test Data", data_items))
    parts.append("")

    parts.append(_table_steps("h1. Steps & Expected Results", steps_pairs))
    parts.append("")

    parts.append(_table_two_cols("h1. Notes and Special Considerations", "Description", notes_items))

    # Preserva extras al final (KPI/Automation/etc.)
    extras_clean = []
    for title, body in extras:
        t = (title or "").strip()
        b = (body or "").strip()
        if not t and not b:
            continue

        # Si el extra ya venía con prefijo "h1. ..." lo normalizamos
        if not t.lower().startswith("h1."):
            extras_clean.append(f"\n\nh1. {t}\n----\n{b}".strip())
        else:
            extras_clean.append(f"\n\n{t}\n----\n{b}".strip())

    if extras_clean:
        parts.append("\n".join(extras_clean).strip())

    # Normaliza saltos
    out = "\n".join([p for p in parts if p is not None]).replace("\r\n", "\n").replace("\r", "\n")
    out = re.sub(r"\n{3,}", "\n\n", out).strip()
    return out
