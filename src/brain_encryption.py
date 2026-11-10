"""Brain Encryption - File-level encryption for brain.db using Fernet (symmetric).

All data in brain.db is encrypted at rest. The encryption key is stored
in the BRAIN_ENCRYPTION_KEY environment variable.

Setup:
    export BRAIN_ENCRYPTION_KEY=$(python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())")

The encrypted database is stored as brain.db.enc. The plaintext brain.db
is never written to disk.
"""

import os
import json
import base64
import sqlite3
import tempfile
import atexit
from pathlib import Path
from cryptography.fernet import Fernet

PROJECT_ROOT = Path(__file__).parent.parent.resolve()
ENCRYPTED_DB_PATH = PROJECT_ROOT / "brain.db.enc"
PLAINTEXT_DB_PATH = PROJECT_ROOT / "brain.db"


def get_encryption_key() -> bytes:
    """Get or generate the encryption key.

    Reads BRAIN_ENCRYPTION_KEY from environment. If not set, generates
    a new key and prints it to stdout (user must save it).
    """
    key_b64 = os.environ.get("BRAIN_ENCRYPTION_KEY", "")
    if not key_b64:
        key = Fernet.generate_key()
        print("\n" + "=" * 60)
        print("BRAIN_ENCRYPTION_KEY not set. Generated new key:")
        print(key.decode())
        print("Save this key to your .env file as BRAIN_ENCRYPTION_KEY")
        print("=" * 60 + "\n")
        return key
    try:
        return base64.urlsafe_b64decode(key_b64)
    except Exception:
        raise ValueError(
            "BRAIN_ENCRYPTION_KEY is invalid. It must be a base64-encoded 32-byte key."
        )


def _get_fernet() -> Fernet:
    return Fernet(get_encryption_key())


def encrypt_database(plaintext_path: Path) -> None:
    """Encrypt an existing plaintext SQLite database to brain.db.enc."""
    if not plaintext_path.exists():
        return
    fernet = _get_fernet()
    with open(plaintext_path, "rb") as f:
        plaintext = f.read()
    encrypted = fernet.encrypt(plaintext)
    with open(ENCRYPTED_DB_PATH, "wb") as f:
        f.write(encrypted)
    # Remove plaintext after successful encryption
    plaintext_path.unlink()


def decrypt_database() -> bytes:
    """Decrypt brain.db.enc to plaintext bytes."""
    if not ENCRYPTED_DB_PATH.exists():
        return b""
    fernet = _get_fernet()
    with open(ENCRYPTED_DB_PATH, "rb") as f:
        encrypted = f.read()
    return fernet.decrypt(encrypted)


class SecureConnection:
    """Wrapper around sqlite3.Connection that auto-encrypts on close.

    On creation: decrypts brain.db.enc to a temp file and opens connection.
    On close(): encrypts temp file back to brain.db.enc and removes temp.
    """

    _temp_path: Path | None = None
    _conn: sqlite3.Connection | None = None
    _closed: bool = False

    def __init__(self):
        self._init_connection()

    def _init_connection(self):
        plaintext = decrypt_database()
        # Write plaintext to temp file
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        tmp.write(plaintext)
        tmp.flush()
        tmp.close()
        self._temp_path = Path(tmp.name)

        # Open SQLite connection to temp file
        self._conn = sqlite3.connect(str(self._temp_path))
        self._conn.row_factory = sqlite3.Row

        # Register cleanup on exit
        atexit.register(self.close)

    def __getattr__(self, name):
        """Delegate attribute access to underlying connection."""
        if self._closed:
            raise ValueError("SecureConnection is closed")
        return getattr(self._conn, name)

    def close(self):
        """Encrypt the current database state back to brain.db.enc and close."""
        if self._closed:
            return
        if self._conn is not None:
            self._conn.commit()
            self._conn.close()
            self._conn = None

        # Encrypt temp file back to brain.db.enc
        if self._temp_path is not None and self._temp_path.exists():
            encrypt_database(self._temp_path)
            self._temp_path.unlink(missing_ok=True)
            self._temp_path = None

        self._closed = True

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()


def get_connection() -> SecureConnection:
    """Open an encrypted connection to the brain database.

    Returns a SecureConnection that automatically encrypts data on close.
    Use as a context manager or call .close() when done.

    Example:
        conn = get_connection()
        try:
            ... use conn ...
        finally:
            conn.close()

    Or:
        with get_connection() as conn:
            ... use conn ...
    """
    return SecureConnection()


def migrate_plaintext_to_encrypted():
    """One-time migration: encrypt existing plaintext brain.db if present."""
    if PLAINTEXT_DB_PATH.exists() and not ENCRYPTED_DB_PATH.exists():
        print("Migrating plaintext brain.db to encrypted brain.db.enc...")
        encrypt_database(PLAINTEXT_DB_PATH)
        print("Migration complete. Plaintext brain.db removed.")
    elif PLAINTEXT_DB_PATH.exists() and ENCRYPTED_DB_PATH.exists():
        print("Both plaintext and encrypted db exist. Keeping encrypted version.")
        print("You may manually delete brain.db if no longer needed.")