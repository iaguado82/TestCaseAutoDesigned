import re
import time


def clean_token(token_str):
    """Limpia el token para evitar errores de formato 400."""
    if not token_str:
        return ""
    t = token_str.strip()
    t = t.replace('"', '').replace("'", "")
    if t.lower().startswith("bearer "):
        t = t[7:].strip()
    t = "".join(char for char in t if 32 < ord(char) < 127)
    return t


def strip_html_tags(text):
    """Elimina etiquetas HTML/XML para reducir el conteo de tokens sin perder el texto."""
    if not text:
        return ""
    clean = re.compile('<.*?>')
    text = re.sub(clean, ' ', text)
    text = re.sub(r'\s+', ' ', text).strip()
    return text


def dump_raw_response(text, us_key, suffix=""):
    """
    Guarda la respuesta cruda de la IA en un fichero para depuración.
    """
    ts = time.strftime("%Y%m%d_%H%M%S")
    suffix_part = f"_{suffix}" if suffix else ""
    filename = f"debug_raw_ai_response_{us_key}{suffix_part}_{ts}.txt"
    try:
        with open(filename, "w", encoding="utf-8") as f:
            f.write(text)
        print(f"DEBUG: Respuesta cruda de la IA guardada en: {filename}", flush=True)
    except Exception as e:
        print(f"DEBUG ERROR: No se pudo guardar la respuesta cruda: {e}", flush=True)

def normalize_jira_wiki(desc: str) -> str:
    if not desc:
        return ""
    s = desc.replace("\r\n", "\n").replace("\r", "\n").strip()
    s = re.sub(r"(?<!\n)(h1\.\s+)", r"\n\1", s)
    s = re.sub(r"\s*----\s*", r"\n----\n", s)
    s = re.sub(r"(?<!\n)\s(\*\s+)", r"\n\1", s)
    s = re.sub(r"(?<!\n)\s(#\s+Acción:)", r"\n\1", s)
    s = re.sub(r"(?<!\n)\nh1\.", r"\n\nh1.", s)
    s = re.sub(r"\n{3,}", "\n\n", s).strip()
    return s.lstrip("\n")
