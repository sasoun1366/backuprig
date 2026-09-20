"""Master-password based encryption for locally stored credentials and backup
content.

Design: a random salt is generated once and stored (unencrypted - salts are
not secret) alongside a small "verifier" token. The master password + salt
are run through PBKDF2-HMAC-SHA256 to derive a Fernet key. The verifier lets
us detect a wrong master password with a clear error instead of garbled data.

Nothing here ever writes the master password itself to disk.
"""

from __future__ import annotations

import base64
import os
from dataclasses import dataclass
from typing import Optional

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

PBKDF2_ITERATIONS = 480_000
SALT_SIZE = 16
VERIFIER_PLAINTEXT = b"backuprig-master-key-verifier-v1"


class WrongMasterPassword(Exception):
    pass


def generate_salt() -> bytes:
    return os.urandom(SALT_SIZE)


def derive_key(password: str, salt: bytes, iterations: int = PBKDF2_ITERATIONS) -> bytes:
    """Derive a urlsafe-base64 Fernet key from a password and salt."""
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=iterations,
    )
    raw = kdf.derive(password.encode("utf-8"))
    return base64.urlsafe_b64encode(raw)


@dataclass
class MasterKey:
    """A derived Fernet key, ready to encrypt/decrypt secrets."""

    fernet: Fernet

    @classmethod
    def new(cls, password: str) -> "tuple[MasterKey, bytes, bytes]":
        """Create a brand new master key. Returns (key, salt, verifier_blob)."""
        salt = generate_salt()
        key = derive_key(password, salt)
        fernet = Fernet(key)
        verifier = fernet.encrypt(VERIFIER_PLAINTEXT)
        return cls(fernet=fernet), salt, verifier

    @classmethod
    def unlock(cls, password: str, salt: bytes, verifier: bytes) -> "MasterKey":
        """Re-derive the key from a password and an existing salt, checking it
        against the stored verifier. Raises WrongMasterPassword if it does not
        match."""
        key = derive_key(password, salt)
        fernet = Fernet(key)
        try:
            plaintext = fernet.decrypt(verifier)
        except InvalidToken:
            raise WrongMasterPassword("incorrect master password")
        if plaintext != VERIFIER_PLAINTEXT:
            raise WrongMasterPassword("incorrect master password")
        return cls(fernet=fernet)

    def encrypt(self, data: bytes) -> bytes:
        return self.fernet.encrypt(data)

    def decrypt(self, token: bytes) -> bytes:
        try:
            return self.fernet.decrypt(token)
        except InvalidToken:
            raise WrongMasterPassword("cannot decrypt data with this master key")

    def encrypt_str(self, text: str) -> bytes:
        return self.encrypt(text.encode("utf-8"))

    def decrypt_str(self, token: bytes) -> str:
        return self.decrypt(token).decode("utf-8")
