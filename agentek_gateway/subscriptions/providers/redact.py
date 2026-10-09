import json
import re

from .base import Headers

LOG_BODY_LIMIT_BYTES = 2048
MASK_LOOKAHEAD_BYTES = 4096

DROPPED_LOG_HEADERS = frozenset(
    {
        "authorization",
        "set-cookie",
        "cookie",
        "chatgpt-account-id",
        "x-codex-turn-state",
    }
)

SECRET_PATTERNS = (
    re.compile(r"eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]*"),
    re.compile(r"\bsk-[A-Za-z0-9*_-]{8,}"),
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{8,}"),
    re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+"),
)

MASK = "***"


def redact_for_log(
    subscription_name: str, status: int, headers: Headers, body: str
) -> str:
    kept = {
        name: value
        for name, value in headers.items()
        if name.lower() not in DROPPED_LOG_HEADERS
    }
    window = body.encode("utf-8", errors="replace")[
        : LOG_BODY_LIMIT_BYTES + MASK_LOOKAHEAD_BYTES
    ]
    masked = mask_secrets(window.decode("utf-8", errors="replace"))
    raw = masked.encode("utf-8")[:LOG_BODY_LIMIT_BYTES].decode(
        "utf-8", errors="replace"
    )
    return json.dumps(
        {
            "subscription": subscription_name,
            "status": status,
            "headers": kept,
            "body": raw,
        },
        ensure_ascii=False,
    )


def mask_secrets(text: str) -> str:
    masked = text
    for pattern in SECRET_PATTERNS:
        masked = pattern.sub(MASK, masked)
    return masked
