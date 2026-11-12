"""Rotate Personal Brain database encryption key.

Usage:
    python scripts/rotate_key.py --old-key <old_base64_key> --new-key <new_base64_key>

Requires BRAIN_DB_KEY set for the current (old) key if --old-key is not provided.
Generates a new base64 key automatically if --new-key is not provided.
"""

import argparse
import base64
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from brain_db import rotate_db_key, DB_PATH


def generate_new_key() -> str:
    """Generate a new base64-encoded 32-byte key."""
    return base64.urlsafe_b64encode(os.urandom(32)).decode()


def main():
    parser = argparse.ArgumentParser(
        description="Rotate Personal Brain database encryption key"
    )
    parser.add_argument(
        "--old-key",
        default=os.environ.get("BRAIN_DB_KEY"),
        help="Current base64 key (default: BRAIN_DB_KEY env var)",
    )
    parser.add_argument(
        "--new-key",
        default=None,
        help="New base64 key (default: auto-generated)",
    )
    args = parser.parse_args()

    if not args.old_key:
        print("Error: --old-key required or set BRAIN_DB_KEY env var", file=sys.stderr)
        return 1

    if not args.new_key:
        args.new_key = generate_new_key()
        print(f"New key generated: {args.new_key}")

    try:
        new_db_path = rotate_db_key(args.old_key, args.new_key)
        print(f"Database rotated successfully: {new_db_path}")
        print(f"Update BRAIN_DB_KEY env var to: {args.new_key}")
        return 0
    except Exception as e:
        print(f"Key rotation failed: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
