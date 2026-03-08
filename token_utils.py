"""
Signed opaque tokens for email URLs.
Replaces plain email addresses in unsubscribe links, tracking pixels, etc.
Uses HMAC-SHA256 with the app's SECRET_KEY.
"""

import hmac
import hashlib
import base64
import json
import os
import time


def _get_signing_key():
    """Return the HMAC signing key from environment."""
    return os.environ.get("SECRET_KEY", "mailenginehub-default-key-change-me").encode()


def create_token(payload: dict, expires_in: int = 0) -> str:
    """
    Create a signed, URL-safe token encoding the given payload.

    Args:
        payload: dict with keys like cid (contact_id), e (email), p (purpose)
        expires_in: seconds until expiry (0 = no expiry)

    Returns:
        URL-safe base64 token string: {payload_b64}.{signature_b64}
    """
    if expires_in > 0:
        payload["exp"] = int(time.time()) + expires_in

    payload_json = json.dumps(payload, separators=(",", ":"), sort_keys=True)
    payload_b64 = base64.urlsafe_b64encode(payload_json.encode()).decode().rstrip("=")

    sig = hmac.new(
        _get_signing_key(),
        payload_b64.encode(),
        hashlib.sha256
    ).digest()
    sig_b64 = base64.urlsafe_b64encode(sig).decode().rstrip("=")

    return f"{payload_b64}.{sig_b64}"


def verify_token(token: str) -> dict:
    """
    Verify and decode a signed token.

    Returns:
        The decoded payload dict, or None if invalid/expired.
    """
    try:
        parts = token.split(".")
        if len(parts) != 2:
            return None

        payload_b64, sig_b64 = parts

        # Verify signature (timing-safe comparison)
        expected_sig = hmac.new(
            _get_signing_key(),
            payload_b64.encode(),
            hashlib.sha256
        ).digest()
        expected_sig_b64 = base64.urlsafe_b64encode(expected_sig).decode().rstrip("=")

        if not hmac.compare_digest(sig_b64, expected_sig_b64):
            return None

        # Decode payload (restore base64 padding)
        padding = 4 - (len(payload_b64) % 4)
        if padding != 4:
            payload_b64 += "=" * padding

        payload_json = base64.urlsafe_b64decode(payload_b64).decode()
        payload = json.loads(payload_json)

        # Check expiry
        if "exp" in payload and payload["exp"] < time.time():
            return None

        return payload
    except Exception:
        return None
