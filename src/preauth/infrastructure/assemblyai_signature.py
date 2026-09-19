"""Verification for AssemblyAI Voice Agent webhook deliveries.

``X-AAI-Signature`` contains ``t=<unix seconds>,v1=<hex digest>``. The digest is HMAC-SHA256 over
``<timestamp>.<raw request body>``. A five-minute tolerance rejects replayed or implausibly future deliveries.
"""

import hashlib
import hmac
import time

from preauth.domain.errors import DomainError

TOLERANCE_SECS = 5 * 60


class AssemblyAIWebhookSignatureError(DomainError):
    code = "WEBHOOK_SIGNATURE_INVALID"


def sign(raw_body: bytes, secret: str, timestamp: int) -> str:
    digest = hmac.new(secret.encode(), f"{timestamp}.".encode() + raw_body, hashlib.sha256).hexdigest()
    return f"t={timestamp},v1={digest}"


def verify(raw_body: bytes, header: str | None, secret: str, *, now: float | None = None) -> None:
    if not header:
        raise AssemblyAIWebhookSignatureError("Missing X-AAI-Signature header")
    try:
        parts = dict(part.split("=", 1) for part in header.split(",") if "=" in part)
    except ValueError:
        parts = {}
    timestamp, signature = parts.get("t"), parts.get("v1")
    if not timestamp or not signature or not timestamp.isdigit():
        raise AssemblyAIWebhookSignatureError("Malformed X-AAI-Signature header")
    current = time.time() if now is None else now
    if abs(current - int(timestamp)) > TOLERANCE_SECS:
        raise AssemblyAIWebhookSignatureError("Webhook signature timestamp is outside the tolerance window")
    expected = sign(raw_body, secret, int(timestamp)).split("v1=", 1)[1]
    if not hmac.compare_digest(expected, signature):
        raise AssemblyAIWebhookSignatureError("Webhook signature does not match")
