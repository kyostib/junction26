import datetime
import json
import logging
import re
import sys
from typing import Any

from app.core import config

logger = logging.getLogger("app.dev_api")

ALLOWED_EXTERNAL_PROVIDERS = ("context.dev", "llm", "gemini", "google-gemini")


def is_external_api_call(provider: str) -> bool:
    """Returns True ONLY for external calls made to LLM and Context.dev, strictly excluding BS Meter API calls."""
    if not provider or not isinstance(provider, str):
        return False
    p = provider.lower().strip()
    # Strictly exclude BS Meter API or local endpoints
    if any(ex in p for ex in ("bsmeter", "bs_meter", "overlay", "internal", "local", "fastapi")):
        return False
    return any(allowed in p for allowed in ALLOWED_EXTERNAL_PROVIDERS)


KEY_REDACT_RE = re.compile(r"([?&]key=)[^&]+")


def _sanitize(obj: Any) -> Any:
    """Recursively redacts API keys, bearer tokens, or secrets from logged queries/responses."""
    if isinstance(obj, dict):
        sanitized = {}
        for k, v in obj.items():
            if any(secret_term in k.lower() for secret_term in ("api_key", "apikey", "secret", "authorization", "password")):
                sanitized[k] = "[REDACTED]"
            else:
                sanitized[k] = _sanitize(v)
        return sanitized
    elif isinstance(obj, list):
        return [_sanitize(x) for x in obj]
    elif isinstance(obj, str):
        return KEY_REDACT_RE.sub(r"\1[REDACTED]", obj)
    return obj


def log_dev_api_call(
    provider: str,
    operation: str,
    query: Any,
    response: Any,
    status: str = "SUCCESS",
    duration_ms: float | None = None,
    error: str | None = None,
) -> None:
    """Logs an API query and response ONLY to the serial output of the container (sys.stdout) in dev mode.
    Strictly excludes BS Meter internal API calls, logging only calls made to LLM and Context.dev.
    """
    settings = config.get_settings()
    if not settings.is_dev or not is_external_api_call(provider):
        return

    timestamp = datetime.datetime.now(datetime.timezone.utc).isoformat()
    duration_str = f" took {duration_ms:.1f}ms" if duration_ms is not None else ""
    error_str = f" | Error: {error}" if error else ""

    def serialize(data: Any) -> str:
        if data is None:
            return "null"
        if hasattr(data, "model_dump"):
            data = data.model_dump()
        elif isinstance(data, list):
            data = [item.model_dump() if hasattr(item, "model_dump") else item for item in data]
        sanitized = _sanitize(data)
        if isinstance(sanitized, (dict, list)):
            try:
                return json.dumps(sanitized, indent=2, ensure_ascii=False, default=str)
            except Exception:
                return str(sanitized)
        return str(sanitized)

    query_str = serialize(query)
    response_str = serialize(response)

    entry = (
        f"{'=' * 80}\n"
        f"[DEV EXTERNAL API CALL]\n"
        f"TIMESTAMP: {timestamp}\n"
        f"PROVIDER: {provider}\n"
        f"OPERATION: {operation}\n"
        f"STATUS: {status}{duration_str}{error_str}\n"
        f"{'-' * 80}\n"
        f"QUERY:\n"
        f"{query_str}\n"
        f"{'-' * 80}\n"
        f"RESPONSE:\n"
        f"{response_str}\n"
        f"{'=' * 80}\n"
    )

    try:
        sys.stdout.write(entry + "\n")
        sys.stdout.flush()
    except Exception as e:
        logger.warning("Failed writing dev API log to stdout: %s", e)
