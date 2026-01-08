import os
import time
import re
import random
import requests

from .utils_text import clean_token


# ----------------------------
# Rate limit parsing helpers
# ----------------------------

def _safe_int(x: str | None) -> int | None:
    if x is None:
        return None
    try:
        return int(str(x).strip())
    except Exception:
        return None


def _extract_wait_seconds_from_text(err_text: str) -> int | None:
    """
    Extrae "Please wait XX seconds" del body si existe.
    OJO: el body NO siempre es fiable, por eso lo usamos como último recurso.
    """
    if not err_text:
        return None
    m = re.search(r"Please wait\s+(\d+)\s+seconds", err_text, re.IGNORECASE)
    if not m:
        return None
    return _safe_int(m.group(1))


def _compute_wait_from_headers(res: requests.Response) -> int | None:
    """
    Intenta obtener el wait (en segundos) a partir de headers estándar.
    Priorización:
      1) Retry-After (segundos)
      2) X-RateLimit-Reset / x-ratelimit-reset (epoch o segundos)
    """
    h = res.headers or {}

    # 1) Retry-After (segundos)
    retry_after = _safe_int(h.get("Retry-After") or h.get("retry-after"))
    if retry_after is not None and retry_after >= 0:
        return retry_after

    # 2) RateLimit reset (puede venir como epoch o como segundos)
    reset = _safe_int(
        h.get("x-ratelimit-reset")
        or h.get("X-RateLimit-Reset")
        or h.get("ratelimit-reset")
        or h.get("RateLimit-Reset")
    )
    if reset is None:
        return None

    now = int(time.time())

    # Heurística:
    # - Si reset es muy grande (>= now), asumimos epoch timestamp.
    # - Si reset es pequeño (< now), asumimos "segundos hasta reset" (menos común).
    if reset >= now:
        return max(0, reset - now)
    else:
        return max(0, reset)


def _pick_sleep_seconds(
    res: requests.Response,
    err_text: str,
    fallback_backoff: int,
    max_sleep_seconds: int,
    jitter_ratio: float = 0.15,
) -> int:
    """
    Decide cuánto dormir en 429.
    - Primero intenta headers (fiables).
    - Luego body ("Please wait X seconds").
    - Si no hay nada, usa backoff exponencial.
    Aplica CAP + jitter.
    """
    wait_s = _compute_wait_from_headers(res)

    if wait_s is None:
        wait_s = _extract_wait_seconds_from_text(err_text)

    if wait_s is None:
        wait_s = fallback_backoff

    # margen defensivo
    wait_s = max(1, int(wait_s) + 1)

    # jitter para evitar sincronización
    jitter = int(wait_s * jitter_ratio)
    if jitter > 0:
        wait_s = wait_s + random.randint(-jitter, jitter)

    # cap final
    wait_s = max(1, min(max_sleep_seconds, wait_s))
    return wait_s


def _log_429_details(res: requests.Response, err_text: str):
    """
    Logging diagnóstico: imprime headers relevantes para rate limit.
    Esto es lo que te permitirá ver si el problema es cuota/tenant
    y si reset viene como epoch.
    """
    h = res.headers or {}

    def g(*names):
        for n in names:
            if n in h:
                return h.get(n)
            low = n.lower()
            for k, v in h.items():
                if k.lower() == low:
                    return v
        return None

    retry_after = g("Retry-After", "retry-after")
    reset = g("x-ratelimit-reset", "X-RateLimit-Reset", "ratelimit-reset", "RateLimit-Reset")
    remaining = g("x-ratelimit-remaining", "X-RateLimit-Remaining", "ratelimit-remaining", "RateLimit-Remaining")
    limit = g("x-ratelimit-limit", "X-RateLimit-Limit", "ratelimit-limit", "RateLimit-Limit")

    # Evita spamear el body entero (puede ser grande); truncamos
    body_preview = (err_text or "").strip()
    if len(body_preview) > 500:
        body_preview = body_preview[:500] + " ...[truncado]"

    print(
        "INFO CONTEXT: 429 details | "
        f"Retry-After={retry_after} | Reset={reset} | Remaining={remaining} | Limit={limit} | "
        f"now_epoch={int(time.time())} | body_preview={body_preview}",
        flush=True,
    )


def _is_daily_quota_exhausted(err_text: str) -> bool:
    """
    Detecta el caso de cuota diaria agotada:
    - 'UserByModelByDay'
    - 'per 86400s'
    En estos casos, NO tiene sentido reintentar en loop con sleeps cortos.
    """
    if not err_text:
        return False
    t = err_text.lower()
    return ("userbymodelbyday" in t) or ("per 86400s" in t)


def _compute_reset_epoch_seconds(res: requests.Response, err_text: str) -> int | None:
    """
    Devuelve una estimación de segundos hasta reset para logging, priorizando headers y luego body.
    No aplica CAP aquí; es informativo.
    """
    w = _compute_wait_from_headers(res)
    if w is None:
        w = _extract_wait_seconds_from_text(err_text)
    return w


# ----------------------------
# Main client
# ----------------------------

def call_github_models(messages, temperature=0.2, timeout=180, max_retries=8):
    """
    Llamada a GitHub Models (Azure inference endpoint) con manejo robusto de rate limit (429).
    - Reintenta automáticamente cuando hay 429.
    - Respeta headers (Retry-After / RateLimit-Reset) cuando existen.
    - CAP de espera para no “colgar” el script horas.
    - FAIL-FAST cuando la cuota diaria está agotada (UserByModelByDay).
    """
    token = clean_token(os.getenv("GITHUB_TOKEN", ""))
    if not token:
        print("ERROR: El token de GitHub está vacío.", flush=True)
        return None

    url = "https://models.inference.ai.azure.com/chat/completions"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }

    payload = {
        "model": "gpt-4o",
        "messages": messages,
        "temperature": temperature,
    }

    # Backoff base si no tenemos señal de espera en headers/body
    backoff = 2

    # CAP: evita sleeps absurdos (si hay cuota agotada “horas”, el script debe fallar rápido o esperar poco)
    MAX_SLEEP_SECONDS = int(os.getenv("GITHUB_MODELS_MAX_SLEEP_SECONDS", "90"))

    for attempt in range(1, max_retries + 1):
        try:
            print(
                f"DEBUG: Consultando IA con contexto (Token len: {len(token)})... "
                f"[intento {attempt}/{max_retries}]",
                flush=True,
            )
            res = requests.post(url, json=payload, headers=headers, timeout=timeout)

            if res.status_code == 200:
                data = res.json()
                return data["choices"][0]["message"]["content"]

            if res.status_code == 429:
                err_text = res.text or ""
                _log_429_details(res, err_text)

                # ---- CAMBIO PROPUESTO (FAIL-FAST en cuota diaria) ----
                if _is_daily_quota_exhausted(err_text):
                    wait_s = _compute_reset_epoch_seconds(res, err_text)
                    if wait_s is not None:
                        reset_epoch = int(time.time()) + max(0, int(wait_s))
                        print(
                            f"ERROR: Cuota diaria agotada (UserByModelByDay). "
                            f"Retry-After={wait_s}s (reset aprox epoch={reset_epoch}). "
                            "Abortando sin reintentos.",
                            flush=True,
                        )
                    else:
                        print(
                            "ERROR: Cuota diaria agotada (UserByModelByDay). Abortando sin reintentos.",
                            flush=True,
                        )
                    return None
                # -----------------------------------------------

                sleep_s = _pick_sleep_seconds(
                    res=res,
                    err_text=err_text,
                    fallback_backoff=backoff,
                    max_sleep_seconds=MAX_SLEEP_SECONDS,
                    jitter_ratio=0.15,
                )

                # Exponencial solo si no venía señal fiable (simplificación):
                # si el backoff estaba siendo usado, lo escalamos.
                # (Si vino Retry-After/reset, normalmente ya te da el tiempo correcto.)
                if sleep_s == backoff or sleep_s == min(MAX_SLEEP_SECONDS, backoff + 1):
                    backoff = min(MAX_SLEEP_SECONDS, backoff * 2)

                print(f"DEBUG WARN IA 429: RateLimitReached. Reintentando tras {sleep_s}s.", flush=True)
                time.sleep(sleep_s)
                continue

            # 413: payload demasiado grande. No reintentes ciegamente aquí.
            if res.status_code == 413:
                print(f"DEBUG ERROR IA 413: Request Too Large. {res.text}", flush=True)
                return None

            # Otros errores: no conviene reintentar ciegamente
            print(f"DEBUG ERROR IA {res.status_code}: {res.text}", flush=True)
            return None

        except requests.exceptions.RequestException as e:
            sleep_s = min(30, backoff)
            backoff = min(60, backoff * 2)
            print(
                f"DEBUG WARN: Error de red/timeout llamando a IA: {e}. Reintentando tras {sleep_s}s.",
                flush=True,
            )
            time.sleep(sleep_s)

        except Exception as e:
            print(f"Error grave en la IA: {e}", flush=True)
            return None

    print("ERROR: Se agotaron los reintentos contra GitHub Models.", flush=True)
    return None
