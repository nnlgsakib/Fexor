"""Kiosk unlock PIN: hashed on the server, checked offline on the kiosk.

A 6-digit PIN has only a million values, so the hash alone doesn't stop a determined attacker
who copied a kiosk's disk; it keeps the PIN out of plain sight. The real protection is the
kiosk's lockout after a few wrong tries, and the hash being stored encrypted on the kiosk.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
from typing import Any

ITERATIONS = 200_000  # about 0.1-0.3 s on a Raspberry Pi: fine for a person typing a PIN


def hash_pin(pin: str, iterations: int = ITERATIONS) -> dict[str, Any]:
    salt = os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", pin.encode(), salt, iterations)
    return {"alg": "pbkdf2-sha256", "iter": iterations,
            "salt": base64.b64encode(salt).decode(), "hash": base64.b64encode(digest).decode()}


def check_pin(pin: str, rec: dict[str, Any] | None) -> bool:
    if not rec or rec.get("alg") != "pbkdf2-sha256":
        return False
    digest = hashlib.pbkdf2_hmac("sha256", pin.encode(), base64.b64decode(rec["salt"]), int(rec["iter"]))
    return hmac.compare_digest(digest, base64.b64decode(rec["hash"]))
