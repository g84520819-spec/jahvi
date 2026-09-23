"""
paystack_client.py
Thin wrapper around Paystack's REST API. Standalone on purpose — this is
the one place that knows Paystack's URL shapes and auth header, so it can
be swapped/mocked/tested without touching the route logic in
paystack_routes.py.
"""

import hashlib
import hmac
import os

import httpx

PAYSTACK_SECRET_KEY = os.getenv("PAYSTACK_SECRET_KEY", "")
PAYSTACK_BASE_URL = "https://api.paystack.co"


def _headers() -> dict:
    if not PAYSTACK_SECRET_KEY:
        raise RuntimeError("PAYSTACK_SECRET_KEY is not set in the environment")
    return {
        "Authorization": f"Bearer {PAYSTACK_SECRET_KEY}",
        "Content-Type": "application/json",
    }


def initialize_transaction(email: str, amount_kobo: int, reference: str) -> dict:
    """Calls Paystack's Initialize Transaction endpoint. Returns the
    'data' object, which includes 'authorization_url' to redirect the
    user to."""
    response = httpx.post(
        f"{PAYSTACK_BASE_URL}/transaction/initialize",
        headers=_headers(),
        json={"email": email, "amount": amount_kobo, "reference": reference},
        timeout=15.0,
    )
    response.raise_for_status()
    body = response.json()
    if not body.get("status"):
        raise RuntimeError(body.get("message", "Paystack initialize failed"))
    return body["data"]


def verify_transaction(reference: str) -> dict:
    """Calls Paystack's Verify Transaction endpoint (server-to-server
    backup check — used right after redirect-back, in case the webhook
    hasn't arrived yet). Returns the 'data' object."""
    response = httpx.get(
        f"{PAYSTACK_BASE_URL}/transaction/verify/{reference}",
        headers=_headers(),
        timeout=15.0,
    )
    response.raise_for_status()
    body = response.json()
    if not body.get("status"):
        raise RuntimeError(body.get("message", "Paystack verify failed"))
    return body["data"]


def verify_webhook_signature(raw_body: bytes, signature_header: str | None) -> bool:
    """Paystack signs every webhook with HMAC-SHA512 of the raw request
    body, using your secret key, sent as the x-paystack-signature header.
    This MUST pass before anything in the webhook payload is trusted —
    otherwise anyone could POST a fake 'charge.success' event."""
    if not signature_header or not PAYSTACK_SECRET_KEY:
        return False
    expected = hmac.new(
        PAYSTACK_SECRET_KEY.encode("utf-8"),
        raw_body,
        hashlib.sha512,
    ).hexdigest()
    return hmac.compare_digest(expected, signature_header)
