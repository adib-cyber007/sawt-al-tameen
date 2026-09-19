"""Short-lived authentication tokens for Twilio's AssemblyAI media WebSocket.

Twilio cannot add an Authorization header to ``<Stream>`` connections, so the signed token travels in the
query string. It contains only Twilio's opaque CallSid and an expiry; no provider or application secret is
exposed to the caller.
"""

import base64
import hashlib
import hmac
import json
import time
from dataclasses import dataclass
from typing import Any


class MediaTokenInvalidError(ValueError):
    """The token was malformed, forged, or expired."""


@dataclass(frozen=True)
class MediaToken:
    call_sid: str
    expires_at: int


def _b64encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _b64decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def issue(call_sid: str, secret: str, *, now: int | None = None, ttl_seconds: int = 90) -> str:
    if not call_sid or not secret or ttl_seconds <= 0:
        raise ValueError("call_sid, secret, and a positive ttl_seconds are required")
    issued_at = int(time.time() if now is None else now)
    payload = _b64encode(
        json.dumps({"call_sid": call_sid, "exp": issued_at + ttl_seconds}, separators=(",", ":")).encode()
    )
    signature = _b64encode(hmac.new(secret.encode(), payload.encode(), hashlib.sha256).digest())
    return f"{payload}.{signature}"


def verify(token: str, secret: str, *, now: int | None = None) -> MediaToken:
    try:
        payload, supplied_signature = token.split(".", 1)
        expected_signature = _b64encode(hmac.new(secret.encode(), payload.encode(), hashlib.sha256).digest())
        if not hmac.compare_digest(supplied_signature, expected_signature):
            raise MediaTokenInvalidError("media token signature is invalid")
        decoded: Any = json.loads(_b64decode(payload))
        call_sid, expires_at = decoded["call_sid"], decoded["exp"]
        if not isinstance(call_sid, str) or not call_sid or not isinstance(expires_at, int):
            raise MediaTokenInvalidError("media token payload is invalid")
    except MediaTokenInvalidError:
        raise
    except (ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
        raise MediaTokenInvalidError("media token is malformed") from exc

    current = int(time.time() if now is None else now)
    if expires_at < current:
        raise MediaTokenInvalidError("media token has expired")
    return MediaToken(call_sid=call_sid, expires_at=expires_at)
