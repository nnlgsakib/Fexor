"""Signed QR tokens (Ed25519).

Token format: ``<PREFIX>.<b64url(json payload)>.<b64url(signature)>``. The prefix is part of
the signed text, so one kind of token can never be used as another.

- ``FX1``  person credential: {"k": key id, "p": person id, "j": credential id}
- ``FXS1`` kiosk settings unlock: {"k": key id, "d": device id, "x": expiry unix time}

The server holds the private key; kiosks verify offline with the synced public keys.
"""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

PREFIX = "FX1"
SETTINGS_PREFIX = "FXS1"
MAX_TOKEN_LEN = 512


class InvalidToken(Exception):
    pass


@dataclass(frozen=True)
class Claims:
    key_id: str
    person_id: str
    credential_id: str


def _b64e(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def _b64d(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def sign_payload(key: Ed25519PrivateKey, prefix: str, payload: dict) -> str:
    body = f"{prefix}.{_b64e(json.dumps(payload, separators=(',', ':')).encode())}"
    return f"{body}.{_b64e(key.sign(body.encode()))}"


def verify_payload(token: str, public_keys: dict[str, Ed25519PublicKey], prefix: str) -> dict:
    if not isinstance(token, str) or len(token) > MAX_TOKEN_LEN:
        raise InvalidToken("malformed")
    parts = token.strip().split(".")
    if len(parts) != 3 or parts[0] != prefix:
        raise InvalidToken("malformed")
    try:
        payload = json.loads(_b64d(parts[1]))
        sig = _b64d(parts[2])
    except (ValueError, json.JSONDecodeError) as e:
        raise InvalidToken("malformed") from e
    if not isinstance(payload, dict):
        raise InvalidToken("malformed")
    key = public_keys.get(str(payload.get("k")))
    if key is None:
        raise InvalidToken("unknown_key")
    try:
        key.verify(sig, f"{parts[0]}.{parts[1]}".encode())
    except InvalidSignature as e:
        raise InvalidToken("bad_signature") from e
    return payload


def sign(key: Ed25519PrivateKey, key_id: str, person_id: str, credential_id: str) -> str:
    return sign_payload(key, PREFIX, {"k": key_id, "p": person_id, "j": credential_id})


def verify(token: str, public_keys: dict[str, Ed25519PublicKey]) -> Claims:
    payload = verify_payload(token, public_keys, PREFIX)
    p, j = payload.get("p"), payload.get("j")
    if not isinstance(p, str) or not isinstance(j, str):
        raise InvalidToken("malformed")
    return Claims(str(payload["k"]), p, j)


def sign_settings(key: Ed25519PrivateKey, key_id: str, device_id: str, expires: int) -> str:
    return sign_payload(key, SETTINGS_PREFIX, {"k": key_id, "d": device_id, "x": expires})


def verify_settings(token: str, public_keys: dict[str, Ed25519PublicKey], device_id: str,
                    now: float) -> None:
    """Raises InvalidToken unless the token unlocks *this* device and has not expired."""
    payload = verify_payload(token, public_keys, SETTINGS_PREFIX)
    if payload.get("d") != device_id:
        raise InvalidToken("other_device")
    x = payload.get("x")
    if not isinstance(x, int) or now > x:
        raise InvalidToken("expired")


def public_key_to_str(key: Ed25519PublicKey) -> str:
    return _b64e(key.public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw))


def public_key_from_str(s: str) -> Ed25519PublicKey:
    return Ed25519PublicKey.from_public_bytes(_b64d(s))
