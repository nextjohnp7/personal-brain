"""Brain Connectors - Sync data from Gmail, Google Calendar,
Twitter/X, and markdown notes folder to SQLite brain."""

import os
import json
from pathlib import Path
from datetime import datetime, timezone

from brain_db import (
    get_connection, upsert_contact, insert_email, upsert_calendar_event,
    insert_tweet, upsert_note, update_contact_interaction, insert_knowledge_entry,
)

# =====================================================================
# 1. GMAIL CONNECTOR (Google OAuth + Gmail API)
# =====================================================================

def sync_gmail(google_creds, max_results=50):
    """Sync emails from Gmail to brain.

    Args:
        google_creds: google.oauth2.credentials.Credentials (valid OAuth token)
        max_results: Maximum number of emails to retrieve

    Returns:
        dict with sync statistics
    """
    from googleapiclient.discovery import build

    service = build('gmail', 'v1', credentials=google_creds)
    conn = get_connection()
    stats = {"emails": 0, "contacts": 0}

    try:
        # Retrieve message ID list
        result = service.users().messages().list(
            userId='me', maxResults=max_results
        ).execute()
        messages = result.get('messages', [])

        for msg in messages:
            msg_data = service.users().messages().get(
                userId='me', id=msg['id'], format='metadata'
            ).execute()

            headers = {h['name']: h['value'] for h in msg_data['payload'].get('headers', [])}
            message_id = msg_data.get('id', '')
            thread_id = msg_data.get('threadId', '')
            sender = headers.get('From', '')
            recipient = headers.get('To', '')
            subject = headers.get('Subject', '')
            snippet = msg_data.get('snippet', '')
            received_at = headers.get('Date', '')

            # Extract email from sender
            sender_email = _extract_email(sender)
            sender_name = _extract_name(sender)

            # Insert or update contact
            contact_id = None
            if sender_email:
                contact_id = upsert_contact(
                    conn, name=sender_name or sender_email,
                    email=sender_email, source='gmail'
                )
                update_contact_interaction(conn, contact_id)
                stats["contacts"] += 1

            # Insert email
            insert_email(
                conn, message_id=message_id, thread_id=thread_id,
                sender=sender, recipient=recipient, subject=subject,
                body=snippet, snippet=snippet, received_at=received_at,
                labels=msg_data.get('labelIds', []), contact_id=contact_id
            )
            stats["emails"] += 1

        conn.close()
        return stats

    except Exception as e:
        conn.close()
        raise RuntimeError(f"Gmail sync error: {e}")


def _extract_email(from_header):
    """Extract email address from From header: 'Name <email@domain.com>'."""
    import re
    if not from_header:
        return None
    match = re.search(r'<([^>]+)>', from_header)
    if match:
        return match.group(1)
    # If no brackets, assume the whole string is an email
    parts = from_header.strip().split()
    for part in parts:
        if '@' in part:
            return part
    return None


def _extract_name(from_header):
    """Extract name from From header."""
    import re
    if not from_header:
        return None
    match = re.match(r'^(.+)<', from_header)
    if match:
        return match.group(1).strip().strip('"')
    return None


# =====================================================================
# 2. GOOGLE CALENDAR CONNECTOR
# =====================================================================

def sync_google_calendar(google_creds, max_results=50):
    """Sync calendar events to brain."""
    from googleapiclient.discovery import build

    service = build('calendar', 'v3', credentials=google_creds)
    conn = get_connection()
    stats = {"events": 0, "contacts": 0}

    try:
        now = datetime.now(timezone.utc).isoformat()
        events_result = service.events().list(
            calendarId='primary',
            timeMin=now,
            maxResults=max_results,
            singleEvents=True,
            orderBy='startTime'
        ).execute()

        events = events_result.get('items', [])

        for event in events:
            event_id = event.get('id', '')
            title = event.get('summary', '(No Title)')
            description = event.get('description', '')
            start = event.get('start', {}).get('dateTime', event.get('start', {}).get('date', ''))
            end = event.get('end', {}).get('dateTime', event.get('end', {}).get('date', ''))
            location = event.get('location', '')
            attendees = event.get('attendees', [])
            attendee_emails = [a.get('email', '') for a in attendees if a.get('email')]

            # Link to contact (organizer or first attendee)
            contact_id = None
            organizer = event.get('organizer', {})
            organizer_email = organizer.get('email', '')
            organizer_name = organizer.get('displayName', '')

            if organizer_email:
                contact_id = upsert_contact(
                    conn, name=organizer_name or organizer_email,
                    email=organizer_email, source='calendar'
                )
                stats["contacts"] += 1
            elif attendee_emails:
                contact_id = upsert_contact(
                    conn, name=attendee_emails[0], email=attendee_emails[0],
                    source='calendar'
                )

            upsert_calendar_event(
                conn, event_id=event_id, title=title, description=description,
                start_time=start, end_time=end, location=location,
                attendees=attendee_emails, contact_id=contact_id
            )
            stats["events"] += 1

        conn.close()
        return stats

    except Exception as e:
        conn.close()
        raise RuntimeError(f"Calendar sync error: {e}")


# =====================================================================
# 3. TWITTER/X CONNECTOR (Twitter API v2)
# =====================================================================

def sync_twitter(bearer_token, username=None, max_results=50):
    """Sync tweets from Twitter/X to brain.

    Args:
        bearer_token: Twitter API v2 Bearer Token
        username: Target username (optional, if None then user timeline)
        max_results: Maximum number of tweets
    """
    import requests

    conn = get_connection()
    stats = {"tweets": 0}

    try:
        headers = {"Authorization": f"Bearer {bearer_token}"}

        if username:
            # Get user ID from username
            user_resp = requests.get(
                f"https://api.twitter.com/2/users/by/username/{username}",
                headers=headers
            )
            user_resp.raise_for_status()
            user_id = user_resp.json()['data']['id']

            # Get tweets from that user
            tweet_resp = requests.get(
                f"https://api.twitter.com/2/users/{user_id}/tweets",
                headers=headers,
                params={"max_results": max_results, "tweet.fields": "created_at,author_id"}
            )
        else:
            # Home timeline (requires OAuth 1.0a user context, not bearer token alone)
            raise ValueError("Bearer token only supports user lookup. Use OAuth 1.0a for timeline.")

        if tweet_resp.status_code == 200:
            tweets = tweet_resp.json().get('data', [])
            for tweet in tweets:
                insert_tweet(
                    conn,
                    tweet_id=tweet['id'],
                    author_username=username or 'unknown',
                    author_name=tweet.get('author_id', ''),
                    content=tweet.get('text', ''),
                    created_at_twitter=tweet.get('created_at', ''),
                    url=f"https://twitter.com/{username}/status/{tweet['id']}"
                )
                stats["tweets"] += 1

        conn.close()
        return stats

    except Exception as e:
        conn.close()
        raise RuntimeError(f"Twitter sync error: {e}")


# =====================================================================
# 4. MARKDOWN NOTES CONNECTOR
# =====================================================================

def sync_markdown_notes(notes_folder):
    """Sync .md files from local folder to brain.

    Args:
        notes_folder: Path to folder containing markdown files
    """
    folder = Path(notes_folder)
    if not folder.exists():
        raise FileNotFoundError(f"Notes folder not found: {notes_folder}")

    conn = get_connection()
    stats = {"notes": 0}

    try:
        for md_file in folder.rglob("*.md"):
            content = md_file.read_text(encoding='utf-8')
            title = _extract_title(md_file.stem, content)
            tags = _extract_tags(content)
            summary = _summarize(content)
            last_modified = datetime.fromtimestamp(
                md_file.stat().st_mtime, tz=timezone.utc
            ).isoformat()

            upsert_note(
                conn,
                filename=md_file.name,
                file_path=str(md_file.resolve()),
                title=title,
                content=content,
                summary=summary,
                tags=tags,
                last_modified=last_modified
            )
            stats["notes"] += 1

        conn.close()
        return stats

    except Exception as e:
        conn.close()
        raise RuntimeError(f"Notes sync error: {e}")


def _extract_title(filename, content):
    """Extract title from H1 markdown or use filename."""
    import re
    match = re.search(r'^#\s+(.+)$', content, re.MULTILINE)
    if match:
        return match.group(1).strip()
    return filename


def _extract_tags(content):
    """Extract tags from markdown (#tag)."""
    import re
    tags = re.findall(r'#(\w+)', content)
    return list(set(tags))[:10]


def _summarize(content, max_chars=200):
    """Simple summary: first paragraph."""
    lines = [l.strip() for l in content.split('\n') if l.strip() and not l.startswith('#')]
    if lines:
        summary = ' '.join(lines)[:max_chars]
        if len(' '.join(lines)) > max_chars:
            summary += '...'
        return summary
    return content[:max_chars]


# =====================================================================
# 5. SYNC ALL (main entry point)
# =====================================================================

def sync_all(google_creds=None, twitter_bearer=None, notes_folder=None):
    """Run sync from all available sources.

    Args:
        google_creds: Google OAuth credentials (for Gmail + Calendar)
        twitter_bearer: Twitter Bearer Token (optional)
        notes_folder: Path to markdown notes folder (optional)

    Returns:
        dict with statistics per source
    """
    results = {}

    if google_creds:
        try:
            results["gmail"] = sync_gmail(google_creds)
        except Exception as e:
            results["gmail"] = {"error": str(e)}
        try:
            results["calendar"] = sync_google_calendar(google_creds)
        except Exception as e:
            results["calendar"] = {"error": str(e)}

    if twitter_bearer:
        try:
            results["twitter"] = sync_twitter(twitter_bearer)
        except Exception as e:
            results["twitter"] = {"error": str(e)}

    if notes_folder:
        try:
            results["notes"] = sync_markdown_notes(notes_folder)
        except Exception as e:
            results["notes"] = {"error": str(e)}

    return results
