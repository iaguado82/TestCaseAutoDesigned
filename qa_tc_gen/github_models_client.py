import os
import time
import json
import re
import requests

from .utils_text import clean_token


def _extract_wait_seconds(err_text: str) -> int | None:
    """
    Extrae "Please wait XX seconds" del mensaje de error si existe.
    Devuelve int o None.
    """
    if not err_text:
        return None
    m = re.search(r"Please wait\s+(\d+)\s+seconds", err_text, re.IGNORECASE)
    if not m:
        return None
    try:
        return int(m.group(1))
    except Exception:
        return None


def call_github_models(messages, temperature=0.2, timeout=180, max_retries=8):
    """
    Llamada a GitHub Models (Azure inference endpoint) con manejo robusto de rate limit (429).
    - Reintenta automáticamente cuando hay 429 (RateLimitReached).
    - Respeta el "Please wait X seconds" si viene en el error.
    """
    token = clean_token(os.getenv("GITHUB_TOKEN", ""))
    if not token:
        print("ERROR: El token de GitHub está vacío.", flush=True)
        return None

    url = "https://models.inference.ai.azure.com/chat/completions"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "application/json"
    }

    payload = {
        "model": "gpt-4o",
        "messages": messages,
        "temperature": temperature
    }

    # Backoff base (si no podemos extraer segundos del mensaje)
    backoff = 2

    for attempt in range(1, max_retries + 1):
        try:
            print(f"DEBUG: Consultando IA con contexto (Token len: {len(token)})... [intento {attempt}/{max_retries}]",
                  flush=True)
            res = requests.post(url, json=payload, headers=headers, timeout=timeout)

            if res.status_code == 200:
                data = res.json()
                return data['choices'][0]['message']['content']

            # Manejo explícito 429: Rate limit por tokens/minuto
            if res.status_code == 429:
                err_text = res.text or ""
                wait_s = _extract_wait_seconds(err_text)

                # Si el backend nos dice cuánto esperar, lo respetamos (con margen)
                if wait_s is not None:
                    sleep_s = max(1, wait_s) + 1  # margen defensivo
                else:
                    # Backoff exponencial conservador si no viene tiempo
                    sleep_s = min(60, backoff)
                    backoff = min(60, backoff * 2)

                print(f"DEBUG WARN IA 429: RateLimitReached. Reintentando tras {sleep_s}s.", flush=True)
                time.sleep(sleep_s)
                continue

            # Otros errores: no conviene reintentar ciegamente
            print(f"DEBUG ERROR IA {res.status_code}: {res.text}", flush=True)
            return None

        except requests.exceptions.RequestException as e:
            # Errores de red puntuales: reintento con backoff corto
            sleep_s = min(30, backoff)
            backoff = min(60, backoff * 2)
            print(f"DEBUG WARN: Error de red/timeout llamando a IA: {e}. Reintentando tras {sleep_s}s.", flush=True)
            time.sleep(sleep_s)

        except Exception as e:
            print(f"Error grave en la IA: {e}", flush=True)
            return None

    print("ERROR: Se agotaron los reintentos contra GitHub Models.", flush=True)
    return None
