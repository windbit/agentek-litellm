import base64
import json
from collections.abc import Mapping
from dataclasses import dataclass

from .chatgpt_json import mapping, number_of, text_of

AUTH_CLAIM = "https://api.openai.com/auth"


@dataclass(frozen=True, slots=True)
class Profile:
    email: str | None
    plan: str | None


def claims_of(token: str) -> Mapping[str, object]:
    parts = token.split(".")
    if len(parts) < 2:
        return {}
    padded = parts[1] + "=" * (-len(parts[1]) % 4)
    try:
        claims = json.loads(base64.urlsafe_b64decode(padded))
    except ValueError:
        return {}
    return mapping(claims) or {}


def jwt_expiry(token: str) -> float | None:
    return number_of(claims_of(token).get("exp"))


def account_id_of(token: str | None) -> str | None:
    if not token:
        return None
    auth = mapping(claims_of(token).get(AUTH_CLAIM))
    return text_of(auth.get("chatgpt_account_id")) if auth else None


def profile_of(id_token: str | None) -> Profile:
    """Account email and plan as the id token states them; they stay readable while the access token is expired."""
    if not id_token:
        return Profile(None, None)
    claims = claims_of(id_token)
    auth = mapping(claims.get(AUTH_CLAIM))
    return Profile(
        email=text_of(claims.get("email")),
        plan=text_of(auth.get("chatgpt_plan_type")) if auth else None,
    )
