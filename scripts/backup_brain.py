"""Backup script for Personal Brain database.

Usage:
    python scripts/backup_brain.py [--output /path/to/backup.db]

Creates a backup of brain.db using SQLite VACUUM INTO (consistent snapshot).
Requires BRAIN_DB_KEY env var if database is encrypted.
"""

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from brain_db import backup_brain_db, PROJECT_ROOT


def main():
    parser = argparse.ArgumentParser(description="Backup Personal Brain database")
    parser.add_argument(
        "--output", "-o",
        default=None,
        help="Output backup file path (default: brain_backup_<timestamp>.db in project root)",
    )
    args = parser.parse_args()

    try:
        backup_path = backup_brain_db(args.output)
        print(f"Backup created: {backup_path}")
        return 0
    except Exception as e:
        print(f"Backup failed: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
