"""Brain Sync Daemon - Background process that auto-syncs the notes folder.

Watches D:/Project GitHub Brain Memory/Notes/ for changes and syncs
new/modified markdown files to brain.db automatically.

Run in background:
    python brain_sync_daemon.py --interval 60
"""

import sys
import os
import time
import json
import argparse
import logging
from pathlib import Path
from datetime import datetime

# Ensure src is importable
sys.path.insert(0, str(Path(__file__).parent / "src"))

from brain_db import (
    get_connection,
    upsert_note,
    search_notes,
)
from brain_connectors import sync_markdown_notes

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[
        logging.FileHandler("brain_daemon.log"),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger("brain-sync-daemon")

PROJECT_ROOT = Path(__file__).parent.resolve()
NOTES_FOLDER = PROJECT_ROOT / "Notes"
STATE_FILE = PROJECT_ROOT / ".brain_daemon_state.json"


def load_state():
    """Load last sync timestamps per file."""
    if STATE_FILE.exists():
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_state(state):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)


def sync_folder_once(conn, folder, state):
    """Sync only new or modified files since last state."""
    folder = Path(folder)
    if not folder.exists():
        logger.warning(f"Notes folder not found: {folder}")
        return state

    changes = 0
    for md_file in folder.rglob("*.md"):
        file_path = str(md_file.resolve())
        mtime = md_file.stat().st_mtime

        if file_path in state and state[file_path] >= mtime:
            continue  # Already synced, no change

        try:
            content = md_file.read_text(encoding="utf-8")
            # Reuse existing logic from brain_connectors
            import re
            from brain_connectors import _extract_title, _extract_tags, _summarize
            from datetime import timezone

            title = _extract_title(md_file.stem, content)
            tags = _extract_tags(content)
            summary = _summarize(content)
            last_modified = datetime.fromtimestamp(
                md_file.stat().st_mtime, tz=timezone.utc
            ).isoformat()

            upsert_note(
                conn,
                filename=md_file.name,
                file_path=file_path,
                title=title,
                content=content,
                summary=summary,
                tags=tags,
                last_modified=last_modified,
            )
            state[file_path] = mtime
            changes += 1
            logger.info(f"Synced: {md_file.name}")
        except Exception as e:
            logger.error(f"Failed to sync {md_file.name}: {e}")

    if changes > 0:
        save_state(state)
        logger.info(f"Sync complete: {changes} file(s) changed")
    return state


def run_daemon(interval_seconds=60, notes_folder=None):
    """Run the daemon loop."""
    if notes_folder is None:
        notes_folder = os.environ.get(
            "BRAIN_NOTES_FOLDER", str(NOTES_FOLDER)
        )

    logger.info(f"Brain Sync Daemon started")
    logger.info(f"Notes folder: {notes_folder}")
    logger.info(f"Sync interval: {interval_seconds} seconds")
    logger.info("Press Ctrl+C to stop")

    state = load_state()
    conn = get_connection()

    try:
        while True:
            try:
                state = sync_folder_once(conn, notes_folder, state)
            except Exception as e:
                logger.error(f"Sync cycle error: {e}")
            time.sleep(interval_seconds)
    except KeyboardInterrupt:
        logger.info("Daemon stopped by user")
    finally:
        conn.close()


def run_once(notes_folder=None):
    """Run a single sync cycle and exit."""
    if notes_folder is None:
        notes_folder = os.environ.get(
            "BRAIN_NOTES_FOLDER", str(NOTES_FOLDER)
        )

    conn = get_connection()
    try:
        state = load_state()
        state = sync_folder_once(conn, notes_folder, state)
        logger.info("Single sync complete")
    finally:
        conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Brain Sync Daemon")
    parser.add_argument(
        "--interval",
        type=int,
        default=60,
        help="Sync interval in seconds (default: 60)",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Run a single sync and exit",
    )
    parser.add_argument(
        "--folder",
        type=str,
        default=None,
        help="Notes folder path (default: BRAIN_NOTES_FOLDER env or ./Notes)",
    )
    args = parser.parse_args()

    if args.once:
        run_once(args.folder)
    else:
        run_daemon(args.interval, args.folder)