"""Password hashing for sign-in accounts.

New hashes use argon2id (argon2-cffi). Stored format: the standard PHC string
"$argon2id$v=19$m=...,t=...,p=...$<salt>$<hash>", so the cost parameters travel with each hash.

Hashes written before M6a use scrypt ("scrypt$<log2 N>$<r>$<p>$<salt b64>$<key b64>"). They
still verify; `needs_rehash` reports them so the sign-in path can replace them with argon2id.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

# argon2id cost profiles.
# "standard": the argon2-cffi default (RFC 9106 low-memory profile: t=3, 64 MiB, p=4).
# "fast": for automated tests only, which create many accounts. Never use it for real accounts.
COST_PROFILES: dict[str, dict[str, int]] = {
    "standard": {"time_cost": 3, "memory_cost": 65536, "parallelism": 4},
    "fast": {"time_cost": 1, "memory_cost": 1024, "parallelism": 1},
}
DEFAULT_PROFILE = "standard"
# Accepted when verifying: a tampered row cannot demand an unbounded amount of memory.
MAX_MEMORY_KIB = 1 << 20  # 1 GiB

MIN_PASSWORD_LENGTH = 10

# Legacy scrypt bounds (verification only).
_SCRYPT_MIN_N_LOG2 = 10
_SCRYPT_MAX_N_LOG2 = 20
_SCRYPT_R = 8
_SCRYPT_P = 1

# No look-alike characters (0/o, 1/l/i), so a generated password can be read off a note.
_ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"

_hashers: dict[str, PasswordHasher] = {}


def _hasher(profile: str) -> PasswordHasher:
    if profile not in COST_PROFILES:
        raise ValueError(f"unknown password cost profile '{profile}'")
    if profile not in _hashers:
        _hashers[profile] = PasswordHasher(**COST_PROFILES[profile])
    return _hashers[profile]


def hash_password(password: str, profile: str = DEFAULT_PROFILE) -> str:
    return _hasher(profile).hash(password)


def _argon2_memory_ok(stored: str) -> bool:
    try:
        params = stored.split("$")[3]  # "m=65536,t=3,p=4"
        values = dict(kv.split("=", 1) for kv in params.split(","))
        return 0 < int(values["m"]) <= MAX_MEMORY_KIB
    except (IndexError, KeyError, ValueError):
        return False


def _verify_scrypt(password: str, stored: str) -> bool:
    try:
        scheme, n_log2_s, r_s, p_s, salt_s, key_s = stored.split("$")
        n_log2 = int(n_log2_s)
        if scheme != "scrypt" or int(r_s) != _SCRYPT_R or int(p_s) != _SCRYPT_P \
                or not _SCRYPT_MIN_N_LOG2 <= n_log2 <= _SCRYPT_MAX_N_LOG2:
            return False
        salt, key = base64.b64decode(salt_s), base64.b64decode(key_s)
    except (ValueError, TypeError):
        return False
    n = 1 << n_log2
    derived = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=n, r=_SCRYPT_R, p=_SCRYPT_P,
                             dklen=len(key) or 32, maxmem=128 * n * _SCRYPT_R + (1 << 20))
    return hmac.compare_digest(derived, key)


def verify_password(password: str, stored: str) -> bool:
    """Constant-time check. Any malformed or out-of-range stored value simply fails."""
    if stored.startswith("$argon2id$"):
        if not _argon2_memory_ok(stored):
            return False
        try:
            return _hasher(DEFAULT_PROFILE).verify(stored, password)
        except (VerifyMismatchError, VerificationError, InvalidHashError):
            return False
    if stored.startswith("scrypt$"):
        return _verify_scrypt(password, stored)
    return False


def needs_rehash(stored: str, profile: str = DEFAULT_PROFILE) -> bool:
    """True for legacy scrypt hashes and for argon2id hashes made with other cost parameters."""
    if not stored.startswith("$argon2id$"):
        return True
    try:
        return _hasher(profile).check_needs_rehash(stored)
    except (InvalidHashError, ValueError):
        return True


def generate_password(groups: int = 3, group_len: int = 4) -> str:
    """Random, readable password such as 'k7mw-x2pq-9rtd' (about 59 bits with the defaults)."""
    return "-".join("".join(secrets.choice(_ALPHABET) for _ in range(group_len)) for _ in range(groups))


def check_new_password(password: str) -> None:
    if len(password) < MIN_PASSWORD_LENGTH:
        raise ValueError(f"password must be at least {MIN_PASSWORD_LENGTH} characters")
    if len(password) > 256:
        raise ValueError("password is too long")
