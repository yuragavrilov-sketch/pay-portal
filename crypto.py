"""
Fernet-based password encryption for SQLAlchemy columns.

Usage in models:
    password = db.Column(EncryptedString(512), nullable=False)

The column stores a Fernet token (base64 string) in the DB.
Reads/writes are transparently encrypted/decrypted.

The Fernet key is read from the FERNET_KEY environment variable (set via .env).
"""
import os
from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import String
from sqlalchemy.types import TypeDecorator


def _get_fernet() -> Fernet:
    key = os.environ.get('FERNET_KEY')
    if not key:
        raise RuntimeError(
            "FERNET_KEY environment variable is not set. "
            "Run `python generate_key.py` to create one."
        )
    try:
        return Fernet(key.encode() if isinstance(key, str) else key)
    except Exception as exc:
        raise RuntimeError(f"Invalid FERNET_KEY: {exc}") from exc


def encrypt(plaintext: str) -> str:
    """Encrypt a plaintext string, return Fernet token as str."""
    if not plaintext:
        return plaintext
    return _get_fernet().encrypt(plaintext.encode()).decode()


def decrypt(token: str) -> str:
    """Decrypt a Fernet token string, return plaintext."""
    if not token:
        return token
    try:
        return _get_fernet().decrypt(token.encode()).decode()
    except InvalidToken:
        # Return raw value if decryption fails (e.g. migrating unencrypted data)
        return token


class EncryptedString(TypeDecorator):
    """
    SQLAlchemy TypeDecorator that transparently encrypts/decrypts
    string values using Fernet symmetric encryption.

    Stored as a VARCHAR containing the Fernet token.
    The token is longer than the plaintext (~60 chars overhead),
    so size the column accordingly (default 512).
    """
    impl = String
    cache_ok = True

    def process_bind_param(self, value, dialect):
        """Called on write: encrypt before storing."""
        if value is None:
            return value
        return encrypt(str(value))

    def process_result_value(self, value, dialect):
        """Called on read: decrypt after loading."""
        if value is None:
            return value
        return decrypt(value)
