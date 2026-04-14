"""RSA-PSS authentication for Kalshi API."""

from __future__ import annotations

import base64
import time
from pathlib import Path

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa


def load_private_key(key_path: Path) -> rsa.RSAPrivateKey:
    """Load an RSA private key from a PEM file."""
    key_data = key_path.read_bytes()
    private_key = serialization.load_pem_private_key(key_data, password=None)
    if not isinstance(private_key, rsa.RSAPrivateKey):
        raise TypeError("Key must be an RSA private key")
    return private_key


def sign_request(
    private_key: rsa.RSAPrivateKey,
    method: str,
    path: str,
    timestamp_ms: int | None = None,
) -> dict[str, str]:
    """Sign a Kalshi API request and return auth headers.

    Args:
        private_key: RSA private key for signing.
        method: HTTP method (GET, POST, DELETE).
        path: Request path without query string (e.g., /trade-api/v2/markets).
        timestamp_ms: Timestamp in milliseconds. If None, uses current time.

    Returns:
        Dict with KALSHI-ACCESS-TIMESTAMP and KALSHI-ACCESS-SIGNATURE headers.
    """
    if timestamp_ms is None:
        timestamp_ms = int(time.time() * 1000)

    message = f"{timestamp_ms}{method.upper()}{path}"
    signature = private_key.sign(
        message.encode(),
        padding.PSS(
            mgf=padding.MGF1(hashes.SHA256()),
            salt_length=hashes.SHA256().digest_size,
        ),
        hashes.SHA256(),
    )
    sig_b64 = base64.b64encode(signature).decode()

    return {
        "KALSHI-ACCESS-TIMESTAMP": str(timestamp_ms),
        "KALSHI-ACCESS-SIGNATURE": sig_b64,
    }
