"""Brain Voice Integration - Caller detection and contact context for phone calls.

Matches incoming phone numbers to contacts in brain.db and provides
full contact context for voice interactions.

Can be extended with Twilio, Vonage, or other telephony services later.
Currently includes a simulated call interface for testing.
"""

import sys
import os
import re
import json
import argparse
import logging
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).parent / "src"))

from brain_db import (
    get_connection,
    search_contacts,
    get_contact,
    get_emails_for_contact,
    upsert_contact,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger("brain-voice")


def normalize_phone(phone: str) -> str:
    """Normalize a phone number for comparison (digits only)."""
    if not phone:
        return ""
    return re.sub(r'\D', '', phone)


def find_contact_by_phone(conn, phone: str):
    """Find a contact by phone number. Returns contact dict or None."""
    normalized = normalize_phone(phone)
    if not normalized:
        return None

    contacts = search_contacts(conn, "", limit=1000)  # Get all contacts
    for c in contacts:
        c_dict = dict(c)
        c_phone = c_dict.get('phone', '')
        if normalize_phone(c_phone) == normalized:
            return c_dict
    return None


def get_caller_context(conn, phone: str):
    """Get full context for an incoming call from a phone number.

    Returns dict with contact info, recent emails, and upcoming events.
    """
    contact = find_contact_by_phone(conn, phone)
    if not contact:
        return {
            "caller": "Unknown",
            "phone": phone,
            "known": False,
            "context": "No matching contact found in brain.",
        }

    cid = contact['id']
    emails = [dict(r) for r in get_emails_for_contact(conn, cid, limit=3)]
    from brain_db import get_upcoming_events
    events = get_upcoming_events(conn, limit=3)

    return {
        "caller": contact['name'],
        "phone": contact.get('phone', phone),
        "email": contact.get('email', ''),
        "company": contact.get('company', ''),
        "role": contact.get('role', ''),
        "known": True,
        "source": contact.get('source', 'manual'),
        "context_notes": contact.get('context_notes', ''),
        "last_interaction": contact.get('last_interaction', ''),
        "recent_emails": [
            {
                "subject": e.get('subject', '(no subject)'),
                "sender": e.get('sender', ''),
                "received_at": e.get('received_at', ''),
            }
            for e in emails
        ],
        "upcoming_events": [
            {
                "title": ev.get('title', '(no title)'),
                "start_time": ev.get('start_time', ''),
                "location": ev.get('location', ''),
            }
            for ev in events
        ],
    }


def format_caller_context(ctx):
    """Format caller context for voice output (TTS-friendly)."""
    if not ctx["known"]:
        return f"Incoming call from {ctx['phone']}. No contact found in your brain."

    lines = [f"Incoming call from {ctx['caller']}."]
    if ctx.get('company'):
        lines.append(f"They work at {ctx['company']}.")
    if ctx.get('role'):
        lines.append(f"Their role is {ctx['role']}.")
    if ctx.get('context_notes'):
        lines.append(f"Notes: {ctx['context_notes']}.")

    if ctx["recent_emails"]:
        lines.append("Recent emails:")
        for e in ctx["recent_emails"][:2]:
            lines.append(f"- {e['subject']} from {e['sender']}.")

    if ctx["upcoming_events"]:
        lines.append("Upcoming calendar events:")
        for ev in ctx["upcoming_events"][:2]:
            lines.append(f"- {ev['title']} on {ev['start_time']}.")

    return " ".join(lines)


def simulate_call(phone: str, notes_folder=None):
    """Simulate an incoming call for testing. Returns formatted context."""
    conn = get_connection()
    try:
        ctx = get_caller_context(conn, phone)
        return format_caller_context(ctx)
    finally:
        conn.close()


def add_phone_contact(name: str, phone: str, email: str = "", company: str = "", role: str = "", notes: str = ""):
    """Add a contact with phone number for voice recognition testing."""
    conn = get_connection()
    try:
        cid = upsert_contact(
            conn,
            name=name,
            email=email or None,
            phone=phone or None,
            company=company or None,
            role=role or None,
            source="voice",
            context_notes=notes or None,
        )
        return f"Contact added: {name} ({phone}) with ID {cid}"
    finally:
        conn.close()


def main():
    parser = argparse.ArgumentParser(description="Brain Voice Integration")
    parser.add_argument(
        "--simulate-call",
        type=str,
        metavar="PHONE",
        help="Simulate an incoming call from PHONE number",
    )
    parser.add_argument(
        "--add-contact",
        nargs="+",
        metavar=("NAME", "PHONE"),
        help="Add a contact with name and phone number",
    )
    parser.add_argument(
        "--list-contacts",
        action="store_true",
        help="List all contacts with phone numbers",
    )
    args = parser.parse_args()

    conn = get_connection()
    try:
        if args.simulate_call:
            result = simulate_call(args.simulate_call)
            print(result)
        elif args.add_contact:
            if len(args.add_contact) < 2:
                print("Error: --add-contact requires NAME and PHONE")
                return
            name, phone = args.add_contact[0], args.add_contact[1]
            extra = {}
            if len(args.add_contact) > 2:
                extra["email"] = args.add_contact[2]
            if len(args.add_contact) > 3:
                extra["company"] = args.add_contact[3]
            if len(args.add_contact) > 4:
                extra["role"] = args.add_contact[4]
            print(add_phone_contact(name, phone, **extra))
        elif args.list_contacts:
            contacts = search_contacts(conn, "", limit=1000)
            phone_contacts = [c for c in contacts if c.get('phone')]
            if not phone_contacts:
                print("No contacts with phone numbers found.")
            else:
                print("# Contacts with Phone Numbers")
                for c in phone_contacts:
                    print(f"- {c['name']}: {c.get('phone', '')} | {c.get('company', '')} | {c.get('role', '')}")
        else:
            parser.print_help()
    finally:
        conn.close()


if __name__ == "__main__":
    main()