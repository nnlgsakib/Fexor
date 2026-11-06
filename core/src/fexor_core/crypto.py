"""AES-256-GCM sealing for biometric data at rest."""

from __future__ import annotations

import base64
import os

import numpy as np
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

NONCE = 12


def load_key(b64: str) -> bytes:
    key = base64.b64decode(b64)
    if len(key) != 32:
        raise ValueError("data key must be 32 bytes (base64)")
    return key


def new_key_b64() -> str:
    return base64.b64encode(AESGCM.generate_key(bit_length=256)).decode()


def seal(key: bytes, plaintext: bytes, aad: bytes = b"") -> bytes:
    nonce = os.urandom(NONCE)
    return nonce + AESGCM(key).encrypt(nonce, plaintext, aad)


def unseal(key: bytes, blob: bytes, aad: bytes = b"") -> bytes:
    return AESGCM(key).decrypt(blob[:NONCE], blob[NONCE:], aad)


def seal_vector(key: bytes, v: np.ndarray, aad: bytes = b"") -> bytes:
    return seal(key, v.astype("<f4").tobytes(), aad)


def unseal_vector(key: bytes, blob: bytes, aad: bytes = b"") -> np.ndarray:
    return np.frombuffer(unseal(key, blob, aad), dtype="<f4").copy()
