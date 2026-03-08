"""
Amazon SNS message signature verification.
Verifies that incoming webhook notifications genuinely come from AWS SNS.
Uses the cryptography library for RSA-SHA1 signature verification.
"""

import base64
import re
import requests
from cryptography.x509 import load_pem_x509_certificate
from cryptography.hazmat.primitives.asymmetric.padding import PKCS1v15
from cryptography.hazmat.primitives.hashes import SHA1

# Cache downloaded signing certificates to avoid re-fetching
_cert_cache = {}

# Allowed SNS certificate URL pattern (must be amazonaws.com)
_CERT_URL_PATTERN = re.compile(
    r"^https://sns\.[a-z0-9-]+\.amazonaws\.com(\.cn)?/"
)


def _validate_cert_url(url):
    """Ensure the SigningCertURL is from amazonaws.com."""
    return bool(_CERT_URL_PATTERN.match(url))


def _get_certificate(url):
    """Download and cache the SNS signing certificate."""
    if url in _cert_cache:
        return _cert_cache[url]

    resp = requests.get(url, timeout=10)
    resp.raise_for_status()
    cert = load_pem_x509_certificate(resp.content)
    _cert_cache[url] = cert
    return cert


def _build_canonical_string(message, msg_type):
    """
    Build the canonical string for SNS signature verification.
    Field order differs by message type per AWS docs.
    """
    if msg_type == "Notification":
        fields = ["Message", "MessageId", "Subject", "Timestamp", "TopicArn", "Type"]
    else:
        # SubscriptionConfirmation or UnsubscribeConfirmation
        fields = ["Message", "MessageId", "SubscribeURL", "Timestamp", "Token", "TopicArn", "Type"]

    parts = []
    for field in fields:
        value = message.get(field)
        if value is not None:
            parts.append(field)
            parts.append(str(value))

    return "\n".join(parts) + "\n"


def verify_sns_message(message: dict) -> tuple:
    """
    Verify an SNS message signature.

    Args:
        message: Parsed JSON body of the SNS HTTP POST

    Returns:
        (is_valid: bool, error_message: str)
    """
    try:
        # Check for required fields
        cert_url = message.get("SigningCertURL", "") or message.get("SigningCertUrl", "")
        if not cert_url:
            return False, "Missing SigningCertURL"

        if not _validate_cert_url(cert_url):
            return False, f"Invalid SigningCertURL domain: {cert_url}"

        signature_b64 = message.get("Signature", "")
        if not signature_b64:
            return False, "Missing Signature"

        msg_type = message.get("Type", "")
        if msg_type not in ("Notification", "SubscriptionConfirmation", "UnsubscribeConfirmation"):
            return False, f"Unknown message Type: {msg_type}"

        # Build canonical string and verify
        canonical = _build_canonical_string(message, msg_type)
        signature = base64.b64decode(signature_b64)

        cert = _get_certificate(cert_url)
        public_key = cert.public_key()

        # SNS uses SHA1WithRSA for SignatureVersion "1"
        public_key.verify(
            signature,
            canonical.encode("utf-8"),
            PKCS1v15(),
            SHA1(),
        )
        return True, ""

    except Exception as e:
        return False, f"Signature verification failed: {str(e)}"
