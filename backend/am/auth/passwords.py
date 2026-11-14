"""Password hashing for sign-in accounts (scrypt, standard library only).

Stored format: scrypt$<log2 N>$<r>$<p>$<salt b64>$<key b64>. The cost parameters travel with
each hash, so the default can be raised later without invalidating existing accounts.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets

# OWASP Password Storage Cheat Sheet, scrypt: N=2^17, r=8, p=1 (about 128 MB per hash).
DEFAULT_N_LOG2 = 17
MIN_N_LOG2 = 10   # floor accepted when verifying (tests use it to stay fast)
MAX_N_LOG2 = 20   # ceiling accepted when verifying: a tampered row cannot demand gigabytes
R = 8
P = 1
KEY_LEN = 32
SALT_LEN = 16
MIN_PASSWORD_LENGTH = 10

# No look-alike characters (0/o, 1/l/i), so a generated password can be read off a note.
_ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"


def _derive(password: str, salt: bytes, n_log2: int) -> bytes:
    n = 1 << n_log2
    return hashlib.scrypt(password.encode("utf-8"), salt=salt, n=n, r=R, p=P, dklen=KEY_LEN,
                          maxmem=128 * n * R + (1 << 20))


def _b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii")


def hash_password(password: str, n_log2: int = DEFAULT_N_LOG2) -> str:
    if not MIN_N_LOG2 <= n_log2 <= MAX_N_LOG2:
        raise ValueError("scrypt cost out of range")
    salt = secrets.token_bytes(SALT_LEN)
    return f"scrypt${n_log2}${R}${P}${_b64(salt)}${_b64(_derive(password, salt, n_log2))}"


def verify_password(password: str, stored: str) -> bool:
    """Constant-time check. Any malformed or out-of-range stored value simply fails."""
    try:
        scheme, n_log2_s, r_s, p_s, salt_s, key_s = stored.split("$")
        n_log2 = int(n_log2_s)
        if scheme != "scrypt" or int(r_s) != R or int(p_s) != P or not MIN_N_LOG2 <= n_log2 <= MAX_N_LOG2:
            return False
        salt, key = base64.b64decode(salt_s), base64.b64decode(key_s)
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(_derive(password, salt, n_log2), key)


def generate_password(groups: int = 3, group_len: int = 4) -> str:
    """Random, readable password such as 'k7mw-x2pq-9rtd' (about 59 bits with the defaults)."""
    return "-".join("".join(secrets.choice(_ALPHABET) for _ in range(group_len)) for _ in range(groups))


def check_new_password(password: str) -> None:
    if len(password) < MIN_PASSWORD_LENGTH:
        raise ValueError(f"password must be at least {MIN_PASSWORD_LENGTH} characters")
    if len(password) > 256:
        raise ValueError("password is too long")
