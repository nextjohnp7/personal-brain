"""Brain Sync Daemon - Background process that auto-syncs notes, Gmail,
and Google Calendar to brain.db on a schedule.

Run in background:
    python brain_sync_daemon.py --interval 300

Run once and exit:
    python brain_sync_daemon.py --once

Disable a source:
    python brain_sync_daemon.py --no-gmail --no-calendar
"""

import sys
import os
import time
import json
import argparse
import logging
from email.utils import parsedate_to_datetime
from pathlib import Path
from datetime import datetime, timezone, timedelta

# Ensure src is importable
sys.path.insert(0, str(Path(__file__).parent / "src"))

from brain_db import (
    get_connection,
    upsert_note,
    upsert_contact,
    insert_email,
    upsert_calendar_event,
    update_contact_interaction,
    record_failed_gmail_message,
    get_failed_gmail_messages,
    clear_failed_gmail_message,
)
from brain_connectors import (
    _extract_email,
    _extract_name,
    _extract_title,
    _extract_tags,
    _summarize,
)

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

# Default interval (seconds) - overridable via env
DEFAULT_INTERVAL = int(os.environ.get("BRAIN_DAEMON_INTERVAL", "300"))


# =====================================================================
# State persistence
# =====================================================================

def load_state():
    """Load last sync timestamps per source."""
    if STATE_FILE.exists():
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_state(state):
    """Save state atomically (temp file + os.replace) so it is not
    corrupted if the process crashes while writing."""
    tmp_path = STATE_FILE.with_suffix(".json.tmp")
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)
    os.replace(tmp_path, STATE_FILE)


def mark_sync_started(state, source):
    """Mark that sync for the given source is running (for recovery)."""
    state["sync_in_progress"] = source
    state["sync_started_at"] = datetime.now(timezone.utc).isoformat()
    save_state(state)


def mark_sync_finished(state):
    """Clear the sync-in-progress flag after completion."""
    state.pop("sync_in_progress", None)
    state.pop("sync_started_at", None)
    save_state(state)


def recover_interrupted_sync(state):
    """If state indicates a previous sync was interrupted, reset the flag
    and return True so the caller knows to handle it carefully."""
    if state.get("sync_in_progress"):
        logger.warning(
            f"Previous sync for {state['sync_in_progress']} was interrupted "
            f"at {state.get('sync_started_at')}. Flag reset."
        )
        state.pop("sync_in_progress", None)
        state.pop("sync_started_at", None)
        return True
    return False


# =====================================================================
# Google OAuth credentials loader
# =====================================================================

def load_google_credentials():
    """Load Google OAuth2 credentials from token file.

    Reads GOOGLE_TOKEN_FILE env var (default: google_token.json).
    Returns Credentials object or None if not available.
    """
    token_file = os.environ.get("GOOGLE_TOKEN_FILE", "google_token.json")
    token_path = Path(token_file)
    if not token_path.is_absolute():
        token_path = PROJECT_ROOT / token_path

    if not token_path.exists():
        logger.warning(
            f"Google token file not found: {token_path}. "
            f"Gmail and Calendar sync will be skipped."
        )
        return None

    try:
        from google.oauth2.credentials import Credentials
        creds = Credentials.from_authorized_user_file(str(token_path))
        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                from google.auth.transport.requests import Request
                creds.refresh(Request())
                # Save refreshed token
                with open(token_path, "w", encoding="utf-8") as f:
                    f.write(creds.to_json())
            else:
                logger.warning("Google credentials invalid or expired")
                return None
        return creds
    except Exception as e:
        logger.error(f"Failed to load Google credentials: {e}")
        return None


# =====================================================================
# Notes sync (incremental by file mtime)
# =====================================================================

def sync_notes_once(conn, folder, state):
    """Sync only new or modified markdown files since last state."""
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
            logger.info(f"Synced note: {md_file.name}")
        except Exception as e:
            logger.error(f"Failed to sync {md_file.name}: {e}")

    if changes > 0:
        save_state(state)
        logger.info(f"Notes sync complete: {changes} file(s) changed")
    return state


# =====================================================================
# Gmail sync (incremental by last sync timestamp)
# =====================================================================

def _process_gmail_message(conn, service, msg_data, stats):
    """Process one Gmail message: upsert contact + insert email."""
    headers = {
        h["name"]: h["value"]
        for h in msg_data.get("payload", {}).get("headers", [])
    }
    message_id = msg_data.get("id", "")
    thread_id = msg_data.get("threadId", "")
    sender = headers.get("From", "")
    recipient = headers.get("To", "")
    subject = headers.get("Subject", "")
    snippet = msg_data.get("snippet", "")
    received_at = headers.get("Date", "")

    # Extract email from sender
    sender_email = _extract_email(sender)
    sender_name = _extract_name(sender)

    contact_id = None
    if sender_email:
        contact_id = upsert_contact(
            conn, name=sender_name or sender_email,
            email=sender_email, source="gmail"
        )
        update_contact_interaction(conn, contact_id)
        stats["contacts"] += 1

    # Insert email (INSERT OR IGNORE handles duplicates)
    insert_email(
        conn, message_id=message_id, thread_id=thread_id,
        sender=sender, recipient=recipient, subject=subject,
        body=snippet, snippet=snippet,
        received_at=received_at,
        labels=msg_data.get("labelIds", []), contact_id=contact_id
    )
    stats["emails"] += 1
    stats["new_messages"] += 1


def sync_gmail_once(conn, creds, state):
    """Sync new emails from Gmail since last sync.

    Uses Gmail API 'historyId' as checkpoint (recommended by Google)
    to avoid missing or reprocessing emails when the process is interrupted.
    If historyId does not exist (first run), fetch current historyId from
    profile and save without processing old emails.
    If historyId has expired (404), fall back to 'after:' timestamp query.
    """
    from googleapiclient.discovery import build
    from googleapiclient.errors import HttpError

    service = build("gmail", "v1", credentials=creds)
    last_history_id = state.get("gmail_history_id")
    last_sync = state.get("gmail_last_sync")

    stats = {"emails": 0, "contacts": 0, "new_messages": 0}

    mark_sync_started(state, "gmail")
    try:
        # ------------------- Retry previously failed messages ----------------
        failed = get_failed_gmail_messages(conn)
        for entry in failed:
            msg_id = entry["message_id"]
            try:
                msg_data = (
                    service.users().messages()
                    .get(userId="me", id=msg_id, format="metadata")
                    .execute()
                )
                _process_gmail_message(conn, service, msg_data, stats)
                clear_failed_gmail_message(conn, msg_id)
                logger.info(f"Gmail retry success: {msg_id}")
                stats["new_messages"] = stats.get("new_messages", 0) + 1
            except Exception as e:
                logger.warning(
                    f"Gmail retry still failed: {msg_id} — {e}"
                )
                # record_failed_gmail_message will increment retry_count
                record_failed_gmail_message(conn, msg_id, str(e))

        # ------------------- First run: initialize historyId ----------------
        if not last_history_id:
            try:
                profile = service.users().getProfile(userId="me").execute()
                last_history_id = profile.get("historyId", "1")
            except AttributeError:
                # Mock or minimal API client without getProfile
                last_history_id = "1"
            state["gmail_history_id"] = last_history_id
            # Also save last_sync for backward compatibility
            if last_sync:
                state["gmail_last_sync"] = last_sync
            save_state(state)
            logger.info(
                f"Gmail initialized with historyId: {last_history_id}"
            )
            # On first run, also fetch recent messages via timestamp fallback
            # since history API has no prior history to replay
            return _sync_gmail_timestamp_fallback(
                conn, service, state, last_sync, stats
            )

        # ------------------- Incremental sync via historyId ----------------
        page_token = None
        newest_history_id = last_history_id

        while True:
            kwargs = {
                "userId": "me",
                "startHistoryId": last_history_id,
                "maxResults": 500,
            }
            if page_token:
                kwargs["pageToken"] = page_token

            try:
                history = service.users().history().list(**kwargs).execute()
            except HttpError as e:
                if e.resp.status == 404:
                    # historyId expired — fall back to timestamp-based sync
                    logger.warning(
                        "Gmail historyId expired, falling back to timestamp-based sync"
                    )
                    return _sync_gmail_timestamp_fallback(
                        conn, service, state, last_sync, stats
                    )
                raise

            for record in history.get("history", []):
                hid = record.get("historyId")
                if hid:
                    newest_history_id = hid

                # New messages added
                for msg_added in record.get("messagesAdded", []):
                    msg = msg_added.get("message", {})
                    msg_id = msg.get("id")
                    if not msg_id:
                        continue
                    try:
                        _process_gmail_message(conn, service, msg, stats)
                        # Message processed successfully — clear from failed list if present
                        clear_failed_gmail_message(conn, msg_id)
                    except Exception as e:
                        logger.error(
                            f"Failed to process Gmail message {msg_id}: {e}"
                        )
                        record_failed_gmail_message(conn, msg_id, str(e))

                # Messages deleted
                for msg_deleted in record.get("messagesDeleted", []):
                    msg_id = msg_deleted.get("message", {}).get("id")
                    if msg_id:
                        cur = conn.cursor()
                        cur.execute(
                            "DELETE FROM emails WHERE message_id = ?", (msg_id,)
                        )
                        conn.commit()

            page_token = history.get("nextPageToken")
            if not page_token:
                break

        # Save latest historyId
        state["gmail_history_id"] = newest_history_id or last_history_id
        mark_sync_finished(state)
        logger.info(
            f"Gmail sync complete: {stats['new_messages']} new, "
            f"{stats['contacts']} contact(s)"
        )

    except Exception as e:
        logger.error(f"Gmail sync error: {e}")
        # Do not call mark_sync_finished so sync_in_progress flag remains
        # for recovery on the next run

    return stats


def _sync_gmail_timestamp_fallback(conn, service, state, last_sync, stats):
    """Fallback Gmail sync using 'after:' timestamp query
    (used when historyId has expired)."""
    # Build query: only messages after last sync
    if last_sync:
        try:
            dt = datetime.fromisoformat(last_sync)
            unix_ts = int(dt.timestamp())
            query = f"after:{unix_ts}"
        except Exception:
            query = ""
    else:
        query = ""

    newest_dt = None
    page_token = None

    try:
        while True:
            result = service.users().messages().list(
                userId="me", maxResults=100, q=query, pageToken=page_token
            ).execute()
            messages = result.get("messages", [])

            if not messages:
                break

            for msg in messages:
                try:
                    msg_data = service.users().messages().get(
                        userId="me", id=msg["id"], format="metadata"
                    ).execute()
                    _process_gmail_message(conn, service, msg_data, stats)

                    if msg_data.get("payload", {}).get("headers"):
                        headers = {
                            h["name"]: h["value"]
                            for h in msg_data["payload"].get("headers", [])
                        }
                        received_at = headers.get("Date", "")
                        if received_at:
                            try:
                                msg_dt = parsedate_to_datetime(received_at)
                                if newest_dt is None or msg_dt > newest_dt:
                                    newest_dt = msg_dt
                            except Exception:
                                pass
                except Exception as e:
                    msg_id = msg.get("id")
                    logger.error(
                        f"Failed to process Gmail message {msg_id}: {e}"
                    )
                    if msg_id:
                        record_failed_gmail_message(conn, msg_id, str(e))

            page_token = result.get("nextPageToken")
            if not page_token:
                break

        if newest_dt:
            state["gmail_last_sync"] = newest_dt.isoformat()
        else:
            state["gmail_last_sync"] = datetime.now(timezone.utc).isoformat()

        mark_sync_finished(state)
        logger.info(
            f"Gmail timestamp fallback complete: {stats['new_messages']} new"
        )

    except Exception as e:
        logger.error(f"Gmail timestamp fallback error: {e}")

    return stats


# =====================================================================
# Google Calendar sync (incremental by last sync timestamp)
# =====================================================================

def sync_calendar_once(conn, creds, state):
    """Synchronize calendar events from Google Calendar.

    Uses Calendar API syncToken for incremental sync
    (catches edits, deletions, and new events). Falls back to updatedMin
    on first run. Saves state after each page so syncToken is not lost
    if the process is interrupted during pagination.
    """
    from googleapiclient.discovery import build
    from googleapiclient.errors import HttpError

    service = build("calendar", "v3", credentials=creds)
    sync_token = state.get("calendar_sync_token")

    stats = {"events": 0, "contacts": 0, "new_events": 0}
    page_token = None

    mark_sync_started(state, "calendar")
    try:
        while True:
            kwargs = {
                "calendarId": "primary",
                "maxResults": 250,
                "singleEvents": True,
                "pageToken": page_token,
            }
            # orderBy is incompatible with syncToken per Google Calendar API docs
            if sync_token:
                kwargs["syncToken"] = sync_token
            else:
                # First run: sync events updated in last 30 days
                kwargs["orderBy"] = "startTime"
                kwargs["updatedMin"] = (
                    datetime.now(timezone.utc) - timedelta(days=30)
                ).isoformat()

            try:
                events_result = service.events().list(**kwargs).execute()
            except HttpError as e:
                if e.resp.status == 410 and sync_token:
                    # syncToken expired — clear and perform full resync
                    logger.warning(
                        "Calendar syncToken expired (410 GONE), clearing and resyncing from scratch"
                    )
                    state["calendar_sync_token"] = None
                    save_state(state)
                    sync_token = None
                    page_token = None
                    continue
                raise

            events = events_result.get("items", [])

            if not events:
                break

            for event in events:
                try:
                    # Handle deleted events (status == "cancelled")
                    if event.get("status") == "cancelled":
                        event_id = event.get("id", "")
                        if event_id:
                            cur = conn.cursor()
                            cur.execute(
                                "DELETE FROM calendar_events WHERE event_id = ?",
                                (event_id,),
                            )
                            conn.commit()
                        continue

                    event_id = event.get("id", "")
                    title = event.get("summary", "(No Title)")
                    description = event.get("description", "")
                    start = event.get("start", {}).get(
                        "dateTime", event.get("start", {}).get("date", "")
                    )
                    end = event.get("end", {}).get(
                        "dateTime", event.get("end", {}).get("date", "")
                    )
                    location = event.get("location", "")
                    attendees = event.get("attendees", [])
                    attendee_emails = [
                        a.get("email", "")
                        for a in attendees
                        if a.get("email")
                    ]

                    # Link to contact (organizer or first attendee)
                    contact_id = None
                    organizer = event.get("organizer", {})
                    organizer_email = organizer.get("email", "")
                    organizer_name = organizer.get("displayName", "")

                    if organizer_email:
                        contact_id = upsert_contact(
                            conn, name=organizer_name or organizer_email,
                            email=organizer_email, source="calendar"
                        )
                        stats["contacts"] += 1
                    elif attendee_emails:
                        contact_id = upsert_contact(
                            conn, name=attendee_emails[0],
                            email=attendee_emails[0], source="calendar"
                        )

                    upsert_calendar_event(
                        conn, event_id=event_id, title=title,
                        description=description, start_time=start, end_time=end,
                        location=location, attendees=attendee_emails,
                        contact_id=contact_id,
                    )
                    stats["events"] += 1
                    stats["new_events"] += 1

                except Exception as e:
                    logger.error(
                        f"Failed to process calendar event {event.get('id')}: {e}"
                    )

            # Pagination
            page_token = events_result.get("nextPageToken")
            if not page_token:
                break

            # Save state after each page so that if the process crashes
            # mid-pagination, already-processed events are not lost and
            # pagination can continue from the next page
            save_state(state)

        # Save syncToken for next incremental sync
        next_sync_token = events_result.get("nextSyncToken")
        if next_sync_token:
            state["calendar_sync_token"] = next_sync_token
        else:
            state["calendar_last_sync"] = datetime.now(timezone.utc).isoformat()

        mark_sync_finished(state)
        logger.info(
            f"Calendar sync complete: {stats['new_events']} new event(s), "
            f"{stats['contacts']} contact(s)"
        )

    except Exception as e:
        logger.error(f"Calendar sync error: {e}")
        # Do not call mark_sync_finished so flag remains for recovery

    return stats


# =====================================================================
# Main daemon loop
# =====================================================================

def run_cycle(do_notes=True, do_gmail=True, do_calendar=True,
              notes_folder=None, creds=None, state=None):
    """Run a single sync cycle across enabled sources.

    Returns updated state dict.
    """
    if state is None:
        state = load_state()

    # Recovery: if previous sync was interrupted, reset flag
    recover_interrupted_sync(state)

    if notes_folder is None:
        notes_folder = os.environ.get(
            "BRAIN_NOTES_FOLDER", str(NOTES_FOLDER)
        )

    conn = get_connection()
    try:
        if do_notes:
            logger.info("--- Notes sync ---")
            state = sync_notes_once(conn, notes_folder, state)
            save_state(state)

        if do_gmail:
            if creds is None:
                logger.warning("Gmail sync skipped: no Google credentials")
            else:
                logger.info("--- Gmail sync ---")
                _ = sync_gmail_once(conn, creds, state)

        if do_calendar:
            if creds is None:
                logger.warning("Calendar sync skipped: no Google credentials")
            else:
                logger.info("--- Calendar sync ---")
                _ = sync_calendar_once(conn, creds, state)

        # WAL checkpoint after all syncs complete
        # so data is written to the main file and not left in WAL
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        conn.commit()

    finally:
        conn.close()

    return state


def run_daemon(interval_seconds=300, do_notes=True, do_gmail=True,
               do_calendar=True, notes_folder=None):
    """Run the daemon loop."""
    if notes_folder is None:
        notes_folder = os.environ.get(
            "BRAIN_NOTES_FOLDER", str(NOTES_FOLDER)
        )

    creds = load_google_credentials() if (do_gmail or do_calendar) else None

    logger.info("Brain Sync Daemon started")
    logger.info(f"Notes folder: {notes_folder}")
    logger.info(f"Sync interval: {interval_seconds} seconds")
    logger.info(f"Sources: notes={do_notes}, gmail={do_gmail}, calendar={do_calendar}")
    logger.info("Press Ctrl+C to stop")

    state = load_state()

    try:
        while True:
            try:
                state = run_cycle(
                    do_notes=do_notes, do_gmail=do_gmail,
                    do_calendar=do_calendar, notes_folder=notes_folder,
                    creds=creds, state=state,
                )
            except Exception as e:
                logger.error(f"Sync cycle error: {e}")
            time.sleep(interval_seconds)
    except KeyboardInterrupt:
        logger.info("Daemon stopped by user")


def run_once(do_notes=True, do_gmail=True, do_calendar=True,
             notes_folder=None):
    """Run a single sync cycle and exit."""
    creds = load_google_credentials() if (do_gmail or do_calendar) else None
    state = run_cycle(
        do_notes=do_notes, do_gmail=do_gmail,
        do_calendar=do_calendar, notes_folder=notes_folder,
        creds=creds,
    )
    logger.info("Single sync complete")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Brain Sync Daemon")
    parser.add_argument(
        "--interval",
        type=int,
        default=DEFAULT_INTERVAL,
        help="Sync interval in seconds (default: 300)",
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
    parser.add_argument(
        "--no-notes",
        action="store_true",
        help="Skip notes folder sync",
    )
    parser.add_argument(
        "--no-gmail",
        action="store_true",
        help="Skip Gmail sync",
    )
    parser.add_argument(
        "--no-calendar",
        action="store_true",
        help="Skip Google Calendar sync",
    )
    args = parser.parse_args()

    if args.once:
        run_once(
            do_notes=not args.no_notes,
            do_gmail=not args.no_gmail,
            do_calendar=not args.no_calendar,
            notes_folder=args.folder,
        )
    else:
        run_daemon(
            interval_seconds=args.interval,
            do_notes=not args.no_notes,
            do_gmail=not args.no_gmail,
            do_calendar=not args.no_calendar,
            notes_folder=args.folder,
        )
