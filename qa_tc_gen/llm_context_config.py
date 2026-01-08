# llm_context_config.py

# Límite duro del modelo (dejamos margen de seguridad)
LLM_MAX_TOKENS = 7800

# Aproximación conservadora chars → tokens
CHARS_PER_TOKEN = 4

# Prioridades de degradación (orden)
DROP_CONFLUENCE_IF_TOO_LARGE = True

# Máximos por bloque
MAX_TRUTH_CHARS = 20000
MAX_CONTEXT_CHARS = 8000
MAX_CONFLUENCE_CHARS = 3000

# Logging
LOG_CONTEXT_DECISIONS = True
