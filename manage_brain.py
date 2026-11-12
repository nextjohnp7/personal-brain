"""Local Brain Manager (Option A) - without Google/Twitter.

Features:
- Add/update contacts manually
- Sync markdown notes from local folder
- Search brain (contacts, notes, knowledge)
- List all contacts and notes
"""

import sys
import os
from pathlib import Path

# Ensure src is importable
sys.path.insert(0, str(Path(__file__).parent / "src"))

from brain_db import (
    get_connection, upsert_contact, search_contacts, get_contact,
    upsert_note, search_notes, global_search, insert_knowledge_entry,
    log_conversation, create_user, get_user_by_username,
)
from brain_connectors import sync_markdown_notes


def add_contact(conn):
    print("\n=== ADD CONTACT ===")
    name = input("Name: ").strip()
    email = input("Email (optional): ").strip() or None
    phone = input("Phone (optional): ").strip() or None
    company = input("Company (optional): ").strip() or None
    role = input("Role (optional): ").strip() or None
    notes = input("Notes (optional): ").strip() or None

    cid = upsert_contact(
        conn, name=name, email=email, phone=phone,
        company=company, role=role,
        source='manual', context_notes=notes
    )
    print(f"Contact saved with ID: {cid}")


def list_contacts(conn):
    print("\n=== CONTACT LIST ===")
    contacts = search_contacts(conn, "", limit=100)
    if not contacts:
        print("(no contacts yet)")
        return
    for c in contacts:
        print(f"#{c['id']} | {c['name']} | {c.get('email', '-')} | {c.get('company', '-')}")


def search_contacts_cmd(conn):
    print("\n=== SEARCH CONTACTS ===")
    q = input("Keyword: ").strip()
    results = search_contacts(conn, q, limit=20)
    if not results:
        print("(not found)")
        return
    for c in results:
        print(f"#{c['id']} | {c['name']} | {c.get('email', '-')} | {c.get('company', '-')}")
        if c.get('context_notes'):
            print(f"    Notes: {c['context_notes']}")


def sync_notes(conn):
    print("\n=== SYNC NOTES ===")
    folder = os.environ.get('BRAIN_NOTES_FOLDER', str(Path(__file__).parent / 'Notes'))
    try:
        stats = sync_markdown_notes(folder)
        print(f"Done. Notes synced: {stats['notes']}")
    except Exception as e:
        print(f"Error: {e}")


def search_notes_cmd(conn):
    print("\n=== SEARCH NOTES ===")
    q = input("Keyword: ").strip()
    results = search_notes(conn, q, limit=20)
    if not results:
        print("(not found)")
        return
    for n in results:
        print(f"#{n['id']} | {n.get('title', '-')} | {n.get('filename', '-')}")
        if n.get('summary'):
            print(f"    Summary: {n['summary'][:150]}")


def search_all(conn):
    print("\n=== SEARCH ENTIRE BRAIN ===")
    q = input("Keyword: ").strip()
    results = global_search(conn, q, limit=10)
    print(f"Contacts: {len(results['contacts'])}")
    print(f"Emails: {len(results['emails'])}")
    print(f"Calendar: {len(results['events'])}")
    print(f"Tweets: {len(results['tweets'])}")
    print(f"Notes: {len(results['notes'])}")
    print(f"Knowledge: {len(results['knowledge'])}")


def create_user_cmd(conn):
    print("\n=== CREATE USER ===")
    user_id = input("User ID (e.g. u1): ").strip()
    username = input("Username: ").strip()
    email = input("Email (optional): ").strip() or None
    password = input("Password: ").strip()
    role = input("Role (user/admin, default: user): ").strip() or "user"

    if not user_id or not username or not password:
        print("User ID, username, and password are required.")
        return

    if get_user_by_username(conn, username):
        print(f"Username '{username}' already exists.")
        return

    # Simple hash placeholder — production uses werkzeug generate_password_hash
    import hashlib
    password_hash = hashlib.sha256(password.encode()).hexdigest()

    try:
        create_user(
            conn, user_id=user_id, username=username,
            email=email, password_hash=password_hash, role=role
        )
        print(f"User '{username}' created (ID: {user_id}, role: {role})")
    except Exception as e:
        print(f"Error: {e}")


def list_users_cmd(conn):
    print("\n=== USER LIST ===")
    cur = conn.cursor()
    cur.execute("SELECT id, username, email, role, created_at FROM users ORDER BY id")
    rows = cur.fetchall()
    if not rows:
        print("(no users yet)")
        return
    for r in rows:
        print(f"#{r[0]} | {r[1]} | {r[2] or '-'} | {r[3]} | {r[4]}")


def main():
    conn = get_connection()
    print("Local Brain ready. Database:", conn)

    while True:
        print("\n=== BRAIN MENU ===")
        print("1. Add contact")
        print("2. List all contacts")
        print("3. Search contacts")
        print("4. Sync notes from folder")
        print("5. Search notes")
        print("6. Search entire brain")
        print("7. Create user")
        print("8. List all users")
        print("0. Exit")

        choice = input("\nSelect: ").strip()

        if choice == '1':
            add_contact(conn)
        elif choice == '2':
            list_contacts(conn)
        elif choice == '3':
            search_contacts_cmd(conn)
        elif choice == '4':
            sync_notes(conn)
        elif choice == '5':
            search_notes_cmd(conn)
        elif choice == '6':
            search_all(conn)
        elif choice == '7':
            create_user_cmd(conn)
        elif choice == '8':
            list_users_cmd(conn)
        elif choice == '0':
            print("Goodbye!")
            break
        else:
            print("Invalid choice.")

    conn.close()


if __name__ == "__main__":
    main()