"""AMBK1 encrypted container: AES-256-GCM with a key derived from a passphrase by scrypt.

Used for database backups and for encrypted ground reports. Layout (format version 1):

    offset  size  field
    0       5     magic b"AMBK1"
    5       16    salt, random per file
    21      12    nonce, random per file (96-bit GCM nonce)
    33      ...   ciphertext followed by the 16-byte GCM tag

key = scrypt(passphrase as UTF-8, salt, n=2**15, r=8, p=1, length 32 bytes)

The 33-byte header is also passed to GCM as associated data, so a change to the magic, salt
or nonce fails authentication exactly like a change to the ciphertext.

Why this design:
  * GCM authenticates as well as encrypts. A wrong passphrase or a modified file is detected
    and reported, never turned into silently corrupt output.
  * scrypt is memory-hard, which makes guessing passphrases for a stolen file expensive.
  * The passphrase only ever exists as a function argument here. Nothing in this module
    stores it, logs it, or puts it in an exception message.

The file can be decrypted outside AM with any scrypt + AES-GCM implementation using the
layout above, or with:  uv run python -m am.admin.crypto <input> <output>
"""

from __future__ import annotations

import argparse
import getpass
import os
import sys
from pathlib import Path
from typing import Any

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

MAGIC = b"AMBK1"
SALT_LEN = 16
NONCE_LEN = 12  # 96 bits: the nonce length GCM is specified for (NIST SP 800-38D)
TAG_LEN = 16
KEY_LEN = 32  # 256-bit key: AES-256
# scrypt cost parameters fixed by the M5 spec for format version 1. Changing them needs a
# new magic, otherwise existing files could no longer be decrypted.
SCRYPT_N = 2**15
SCRYPT_R = 8
SCRYPT_P = 1
HEADER_LEN = len(MAGIC) + SALT_LEN + NONCE_LEN
# The cryptography package encrypts in one call and accepts at most 2**31 - 1 bytes.
MAX_PLAINTEXT_BYTES = 2**31 - 1
MIN_PASSPHRASE_CHARS = 12

FORMAT_DESCRIPTION: dict[str, Any] = {
    "format": "AMBK1",
    "cipher": "AES-256-GCM",
    "kdf": {"name": "scrypt", "n": SCRYPT_N, "r": SCRYPT_R, "p": SCRYPT_P, "salt_bytes": SALT_LEN},
    "nonce_bytes": NONCE_LEN,
    "associated_data": "33-byte header (magic, salt, nonce)",
}


class DecryptionError(Exception):
    """The blob is not an AMBK1 container, or authentication failed.

    reason: not_container | auth_failed. Authentication failure means a wrong passphrase
    or a modified file; GCM cannot tell the two apart (the caller can, with a checksum).
    """

    def __init__(self, reason: str, message: str):
        super().__init__(message)
        self.reason = reason


def check_passphrase(passphrase: str) -> None:
    if len(passphrase) < MIN_PASSPHRASE_CHARS:
        raise ValueError(f"passphrase must be at least {MIN_PASSPHRASE_CHARS} characters")


def derive_key(passphrase: str, salt: bytes) -> bytes:
    return Scrypt(salt=salt, length=KEY_LEN, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P).derive(passphrase.encode("utf-8"))


def encrypt_bytes(plaintext: bytes, passphrase: str) -> bytes:
    check_passphrase(passphrase)
    if len(plaintext) > MAX_PLAINTEXT_BYTES:
        raise ValueError("data is too large for a single AMBK1 container (limit 2 GiB)")
    salt, nonce = os.urandom(SALT_LEN), os.urandom(NONCE_LEN)
    header = MAGIC + salt + nonce
    return header + AESGCM(derive_key(passphrase, salt)).encrypt(nonce, plaintext, header)


def is_container(blob: bytes) -> bool:
    return blob[: len(MAGIC)] == MAGIC and len(blob) >= HEADER_LEN + TAG_LEN


def decrypt_bytes(blob: bytes, passphrase: str) -> bytes:
    if not is_container(blob):
        raise DecryptionError("not_container", "not an AM encrypted file (AMBK1 header missing or file truncated)")
    header = blob[:HEADER_LEN]
    salt = header[len(MAGIC):len(MAGIC) + SALT_LEN]
    nonce = header[len(MAGIC) + SALT_LEN:]
    try:
        return AESGCM(derive_key(passphrase, salt)).decrypt(nonce, blob[HEADER_LEN:], header)
    except InvalidTag:
        raise DecryptionError("auth_failed", "wrong passphrase, or the file was modified") from None


def _read_passphrase(from_stdin: bool) -> str:
    if from_stdin:
        return sys.stdin.readline().rstrip("\r\n")
    return getpass.getpass("Passphrase: ")


def main(argv: list[str] | None = None) -> int:
    """Decrypt an AMBK1 file (backup or ground report) to a new file.

    The passphrase is read from the terminal (or one line of stdin), never from the command
    line, where it would end up in shell history and process listings.
    """
    parser = argparse.ArgumentParser(prog="python -m am.admin.crypto",
                                     description="Decrypt an AM encrypted file (AMBK1) to a new file.")
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--passphrase-stdin", action="store_true",
                        help="read the passphrase from the first line of standard input")
    args = parser.parse_args(argv)
    if args.output.exists():
        print(f"refusing to overwrite {args.output}", file=sys.stderr)
        return 2
    try:
        plain = decrypt_bytes(args.input.read_bytes(), _read_passphrase(args.passphrase_stdin))
    except (OSError, DecryptionError) as exc:
        print(f"decryption failed: {exc}", file=sys.stderr)
        return 1
    args.output.write_bytes(plain)
    print(f"wrote {args.output} ({len(plain)} bytes)")
    return 0


if __name__ == "__main__":  # pragma: no cover - thin CLI wrapper
    raise SystemExit(main())
