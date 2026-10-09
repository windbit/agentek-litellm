import json
import math
from collections.abc import Mapping

from .redact import LOG_BODY_LIMIT_BYTES


def json_object(text: str) -> Mapping[str, object] | None:
    try:
        parsed = json.loads(text)
    except ValueError:
        return None
    return mapping(parsed)


def mapping(value: object) -> Mapping[str, object] | None:
    return value if isinstance(value, Mapping) else None


def error_object(payload: Mapping[str, object] | None) -> Mapping[str, object] | None:
    return mapping(payload.get("error")) if payload else None


def error_text(
    payload: Mapping[str, object] | None, error: Mapping[str, object] | None, raw: str
) -> str:
    detail = text_of(payload.get("detail")) if payload else None
    message = text_of(error.get("message")) if error else None
    return detail or message or raw[:LOG_BODY_LIMIT_BYTES]


def text_of(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def number_of(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return finite(float(value))
    if isinstance(value, str):
        try:
            return finite(float(value))
        except ValueError:
            return None
    return None


def finite(value: float) -> float | None:
    return value if math.isfinite(value) else None
