"""One-time migration: decrypt old Fernet brain.db.enc -> SQLCipher brain.db.

Reuses the same BRAIN_DB_KEY (Fernet key = base64 of 32 bytes) as the SQLCipher
raw key. Keeps the old file as brain.db.enc.bak until you are confident.

Prerequisites:
    pip install sqlcipher3 cryptography

Run:
    BRAIN_DB_KEY=<your-key> python migrate_to_sqlcipher.py
"""

import base64
import os
import sys
import tempfile
from pathlib import Path
from cryptography.fernet import Fernet
import sqlcipher3

ROOT = Path(__file__).parent.resolve()
OLD_ENC = ROOT / "brain.db.enc"
NEW_DB = ROOT / "brain.db"

if not OLD_ENC.exists():
    sys.exit("brain.db.enc not found - nothing to migrate.")
if NEW_DB.exists():
    sys.exit("brain.db already exists - move it aside first (refusing to overwrite).")

key_b64 = os.environ.get("BRAIN_DB_KEY")
if not key_b64:
    sys.exit("BRAIN_DB_KEY environment variable is required.")

key_hex = base64.urlsafe_b64decode(key_b64).hex()

# Step 1: decrypt old Fernet file to a temp plaintext SQLite
tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
tmp.write(Fernet(key_b64.encode()).decrypt(OLD_ENC.read_bytes()))
tmp.close()

# Step 2: attach new SQLCipher database and export schema + data into it
conn = sqlcipher3.connect(tmp.name)
conn.execute(
    f"ATTACH DATABASE '{NEW_DB.as_posix()}' AS encrypted KEY \"x'{key_hex}'\""
)
conn.execute("SELECT sqlcipher_export('encrypted')")
conn.execute("DETACH DATABASE encrypted")
conn.close()

# Step 3: verify the new database opens with the key
check = sqlcipher3.connect(str(NEW_DB))
check.execute(f'PRAGMA key = "x\'{key_hex}\'"')
n = check.execute("SELECT count(*) FROM sqlite_master").fetchone()[0]
check.close()
assert n > 0, "Migration verification failed!"

# Step 4: cleanup
os.unlink(tmp.name)  # hapus plaintext temp
OLD_ENC.rename(ROOT / "brain.db.enc.bak")  # simpan backup sampai yakin
print(f"Done: {n} schema objects migrated. Old file kept as brain.db.enc.bak")
