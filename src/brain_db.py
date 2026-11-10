"""Personal Brain Database - SQLite lokal untuk knowledge base agen.

Semua data tersimpan di satu file SQLite di komputer user.
Tidak ada server yang diperlukan.
"""

import sqlite3
import json
import os
from pathlib import Path
from datetime import datetime

# Lokasi database di project root
PROJECT_ROOT = Path(__file__).parent.parent.resolve()
DB_PATH = PROJECT_ROOT / "brain.db"


def get_connection():
    """Buka koneksi ke SQLite database. Buat tabel jika belum ada."""
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    _create_tables(conn)
    return conn


def _create_tables(conn):
    """Inisialisasi semua tabel brain."""
    cur = conn.cursor()

    # Kontak orang yang dikenal agen
    cur.execute("""
        CREATE TABLE IF NOT EXISTS contacts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            email TEXT,
            phone TEXT,
            company TEXT,
            role TEXT,
            source TEXT,           -- 'gmail', 'calendar', 'twitter', 'manual'
            last_interaction TEXT, -- ISO timestamp
            context_notes TEXT,    -- catatan bebas tentang orang ini
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
        )
    """)

    # Email dari Gmail
    cur.execute("""
        CREATE TABLE IF NOT EXISTS emails (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            message_id TEXT UNIQUE,
            thread_id TEXT,
            sender TEXT,
            recipient TEXT,
            subject TEXT,
            body TEXT,
            snippet TEXT,
            received_at TEXT,
            labels TEXT,           -- JSON array
            contact_id INTEGER,    -- FK ke contacts
            created_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (contact_id) REFERENCES contacts(id)
        )
    """)

    # Event kalender dari Google Calendar
    cur.execute("""
        CREATE TABLE IF NOT EXISTS calendar_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_id TEXT UNIQUE,
            title TEXT,
            description TEXT,
            start_time TEXT,
            end_time TEXT,
            location TEXT,
            attendees TEXT,        -- JSON array of emails
            contact_id INTEGER,    -- FK ke contacts (organizer atau primary attendee)
            source TEXT DEFAULT 'google_calendar',
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (contact_id) REFERENCES contacts(id)
        )
    """)

    # Tweet dari Twitter/X
    cur.execute("""
        CREATE TABLE IF NOT EXISTS tweets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tweet_id TEXT UNIQUE,
            author_username TEXT,
            author_name TEXT,
            content TEXT,
            created_at_twitter TEXT,
            retrieved_at TEXT DEFAULT (datetime('now')),
            url TEXT
        )
    """)

    # Catatan markdown dari folder lokal
    cur.execute("""
        CREATE TABLE IF NOT EXISTS notes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            filename TEXT NOT NULL,
            file_path TEXT NOT NULL,
            title TEXT,
            content TEXT,
            summary TEXT,
            tags TEXT,             -- JSON array
            last_modified TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
        )
    """)

    # Riwayat percakapan agen (untuk belajar seiring waktu)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS conversations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_message TEXT,
            agent_response TEXT,
            context_used TEXT,     -- JSON array of source IDs yang digunakan
            created_at TEXT DEFAULT (datetime('now'))
        )
    """)

    # Entri knowledge bebas (findings, connections between people, ide)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS knowledge_entries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            entry_type TEXT,       -- 'person_connection', 'idea', 'finding', 'preference'
            title TEXT,
            content TEXT,
            related_contact_ids TEXT, -- JSON array of contact IDs
            source TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
        )
    """)

    conn.commit()


# =====================================================================
# Operasi Kontak
# =====================================================================

def upsert_contact(conn, name, email=None, phone=None, company=None, role=None,
                   source='manual', context_notes=None):
    """Masukkan atau update kontak. Return contact_id."""
    cur = conn.cursor()
    # Cari kontak existing berdasarkan email atau nama
    cur.execute("SELECT id FROM contacts WHERE email = ? OR name = ?", (email, name))
    row = cur.fetchone()
    now = datetime.utcnow().isoformat()

    if row:
        contact_id = row['id']
        cur.execute("""
            UPDATE contacts
            SET phone = COALESCE(?, phone),
                company = COALESCE(?, company),
                role = COALESCE(?, role),
                source = COALESCE(?, source),
                context_notes = COALESCE(?, context_notes),
                updated_at = ?
            WHERE id = ?
        """, (phone, company, role, source, context_notes, now, contact_id))
    else:
        cur.execute("""
            INSERT INTO contacts (name, email, phone, company, role, source,
                                  context_notes, last_interaction)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (name, email, phone, company, role, source, context_notes, now))
        contact_id = cur.lastrowid

    conn.commit()
    return contact_id


def get_contact(conn, contact_id):
    cur = conn.cursor()
    cur.execute("SELECT * FROM contacts WHERE id = ?", (contact_id,))
    return cur.fetchone()


def search_contacts(conn, query, limit=20):
    """Cari kontak berdasarkan nama, email, atau catatan."""
    cur = conn.cursor()
    like = f"%{query}%"
    cur.execute("""
        SELECT * FROM contacts
        WHERE name LIKE ? OR email LIKE ? OR company LIKE ? OR context_notes LIKE ?
        ORDER BY name
        LIMIT ?
    """, (like, like, like, like, limit))
    return cur.fetchall()


def update_contact_interaction(conn, contact_id):
    """Tandai terakhir berinteraksi dengan kontak ini."""
    now = datetime.utcnow().isoformat()
    cur = conn.cursor()
    cur.execute("UPDATE contacts SET last_interaction = ?, updated_at = ? WHERE id = ?",
                (now, now, contact_id))
    conn.commit()


# =====================================================================
# Operasi Email
# =====================================================================

def insert_email(conn, message_id, thread_id, sender, recipient, subject, body,
                 snippet, received_at, labels=None, contact_id=None):
    cur = conn.cursor()
    labels_json = json.dumps(labels or [])
    cur.execute("""
        INSERT OR IGNORE INTO emails
        (message_id, thread_id, sender, recipient, subject, body, snippet,
         received_at, labels, contact_id)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (message_id, thread_id, sender, recipient, subject, body, snippet,
          received_at, labels_json, contact_id))
    conn.commit()
    return cur.lastrowid


def search_emails(conn, query, limit=20):
    """Cari email berdasarkan subjek, isi, atau pengirim."""
    cur = conn.cursor()
    like = f"%{query}%"
    cur.execute("""
        SELECT * FROM emails
        WHERE subject LIKE ? OR body LIKE ? OR sender LIKE ? OR recipient LIKE ?
        ORDER BY received_at DESC
        LIMIT ?
    """, (like, like, like, like, limit))
    return cur.fetchall()


def get_emails_for_contact(conn, contact_id, limit=10):
    cur = conn.cursor()
    cur.execute("""
        SELECT * FROM emails WHERE contact_id = ?
        ORDER BY received_at DESC LIMIT ?
    """, (contact_id, limit))
    return cur.fetchall()


# =====================================================================
# Operasi Kalender
# =====================================================================

def upsert_calendar_event(conn, event_id, title, description, start_time, end_time,
                          location=None, attendees=None, contact_id=None):
    cur = conn.cursor()
    attendees_json = json.dumps(attendees or [])
    now = datetime.utcnow().isoformat()
    cur.execute("SELECT id FROM calendar_events WHERE event_id = ?", (event_id,))
    row = cur.fetchone()
    if row:
        cur.execute("""
            UPDATE calendar_events
            SET title = ?, description = ?, start_time = ?, end_time = ?,
                location = ?, attendees = ?, contact_id = ?, updated_at = ?
            WHERE event_id = ?
        """, (title, description, start_time, end_time, location, attendees_json,
              contact_id, now, event_id))
    else:
        cur.execute("""
            INSERT INTO calendar_events
            (event_id, title, description, start_time, end_time, location,
             attendees, contact_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (event_id, title, description, start_time, end_time, location,
              attendees_json, contact_id))
    conn.commit()
    return cur.lastrowid


def get_upcoming_events(conn, limit=10):
    """Ambil event kalender mendatang."""
    now = datetime.utcnow().isoformat()
    cur = conn.cursor()
    cur.execute("""
        SELECT * FROM calendar_events
        WHERE start_time >= ?
        ORDER BY start_time ASC
        LIMIT ?
    """, (now, limit))
    return cur.fetchall()


# =====================================================================
# Operasi Tweet
# =====================================================================

def insert_tweet(conn, tweet_id, author_username, author_name, content,
                 created_at_twitter, url=None):
    cur = conn.cursor()
    cur.execute("""
        INSERT OR IGNORE INTO tweets
        (tweet_id, author_username, author_name, content, created_at_twitter, url)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (tweet_id, author_username, author_name, content, created_at_twitter, url))
    conn.commit()
    return cur.lastrowid


# =====================================================================
# Operasi Notes
# =====================================================================

def upsert_note(conn, filename, file_path, title, content, summary=None,
                tags=None, last_modified=None):
    cur = conn.cursor()
    tags_json = json.dumps(tags or [])
    now = datetime.utcnow().isoformat()
    cur.execute("SELECT id FROM notes WHERE file_path = ?", (file_path,))
    row = cur.fetchone()
    if row:
        cur.execute("""
            UPDATE notes
            SET title = ?, content = ?, summary = ?, tags = ?,
                last_modified = ?, updated_at = ?
            WHERE file_path = ?
        """, (title, content, summary, tags_json, last_modified, now, file_path))
    else:
        cur.execute("""
            INSERT INTO notes
            (filename, file_path, title, content, summary, tags,
             last_modified, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (filename, file_path, title, content, summary, tags_json,
              last_modified, now, now))
    conn.commit()
    return cur.lastrowid


def search_notes(conn, query, limit=20):
    """Cari catatan berdasarkan judul, isi, atau tag."""
    cur = conn.cursor()
    like = f"%{query}%"
    cur.execute("""
        SELECT * FROM notes
        WHERE title LIKE ? OR content LIKE ? OR tags LIKE ?
        ORDER BY updated_at DESC
        LIMIT ?
    """, (like, like, like, limit))
    return cur.fetchall()


# =====================================================================
# Operasi Knowledge Entries
# =====================================================================

def insert_knowledge_entry(conn, entry_type, title, content,
                           related_contact_ids=None, source=None):
    cur = conn.cursor()
    related_json = json.dumps(related_contact_ids or [])
    now = datetime.utcnow().isoformat()
    cur.execute("""
        INSERT INTO knowledge_entries
        (entry_type, title, content, related_contact_ids, source,
         created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (entry_type, title, content, related_json, source, now, now))
    conn.commit()
    return cur.lastrowid


def search_knowledge(conn, query, limit=20):
    cur = conn.cursor()
    like = f"%{query}%"
    cur.execute("""
        SELECT * FROM knowledge_entries
        WHERE title LIKE ? OR content LIKE ?
        ORDER BY created_at DESC
        LIMIT ?
    """, (like, like, limit))
    return cur.fetchall()


# =====================================================================
# Operasi Conversations
# =====================================================================

def log_conversation(conn, user_message, agent_response, context_used=None):
    cur = conn.cursor()
    context_json = json.dumps(context_used or [])
    cur.execute("""
        INSERT INTO conversations (user_message, agent_response, context_used)
        VALUES (?, ?, ?)
    """, (user_message, agent_response, context_json))
    conn.commit()
    return cur.lastrowid


# =====================================================================
# Search Global (di semua tabel)
# =====================================================================

def global_search(conn, query, limit=10):
    """Cari di kontak, email, kalender, tweet, notes, dan knowledge."""
    results = {
        "contacts": [dict(r) for r in search_contacts(conn, query, limit)],
        "emails": [dict(r) for r in search_emails(conn, query, limit)],
        "events": [dict(r) for r in get_upcoming_events(conn, limit)],
        "tweets": [],  # tweets search via LIKE
        "notes": [dict(r) for r in search_notes(conn, query, limit)],
        "knowledge": [dict(r) for r in search_knowledge(conn, query, limit)],
    }
    # Search tweets
    cur = conn.cursor()
    like = f"%{query}%"
    cur.execute("""
        SELECT * FROM tweets WHERE content LIKE ? OR author_username LIKE ?
        ORDER BY retrieved_at DESC LIMIT ?
    """, (like, like, limit))
    results["tweets"] = [dict(r) for r in cur.fetchall()]
    return results