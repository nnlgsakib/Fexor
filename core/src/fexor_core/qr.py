"""Signed QR credentials (Ed25519).

Token format: ``FX1.<b64url(json payload)>.<b64url(signature)>``
Payload: {"k": key id, "p": person id, "j": credential id}
The server holds the private key; kiosks verify offline with public keys and
check ``j`` against a synced revocation list.
"""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

PREFIX = "FX1"
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


def sign(key: Ed25519PrivateKey, key_id: str, person_id: str, credential_id: str) -> str:
    payload = json.dumps(
        {"k": key_id, "p": person_id, "j": credential_id}, separators=(",", ":")
    ).encode()
    body = f"{PREFIX}.{_b64e(payload)}"
    return f"{body}.{_b64e(key.sign(body.encode()))}"


def verify(token: str, public_keys: dict[str, Ed25519PublicKey]) -> Claims:
    if not isinstance(token, str) or len(token) > MAX_TOKEN_LEN:
        raise InvalidToken("malformed")
    parts = token.strip().split(".")
    if len(parts) != 3 or parts[0] != PREFIX:
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
    p, j = payload.get("p"), payload.get("j")
    if not isinstance(p, str) or not isinstance(j, str):
        raise InvalidToken("malformed")
    return Claims(str(payload["k"]), p, j)


def public_key_to_str(key: Ed25519PublicKey) -> str:
    return _b64e(key.public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw))


def public_key_from_str(s: str) -> Ed25519PublicKey:
    return Ed25519PublicKey.from_public_bytes(_b64d(s))
