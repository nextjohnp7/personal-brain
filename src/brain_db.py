"""Personal Brain Database - SQLite database for agent knowledge base.

All data is stored in a single SQLite file on the user's computer.
No server is required.
"""

import base64
import json
import os
import re
from pathlib import Path
from datetime import datetime

import sqlcipher3 as sqlite3
from brain_embeddings import embed_text, cosine_similarity as _cosine_similarity

# Database location at project root
PROJECT_ROOT = Path(__file__).parent.parent.resolve()
DB_PATH = PROJECT_ROOT / "brain.db"
ENCRYPTED_PATH = PROJECT_ROOT / "brain.db.enc"


def _get_key_hex():
    """Reuse BRAIN_DB_KEY (Fernet key = base64 of 32 bytes) as SQLCipher raw key."""
    key_b64 = os.environ.get("BRAIN_DB_KEY")
    if not key_b64:
        return None
    raw = base64.urlsafe_b64decode(key_b64)
    if len(raw) != 32:
        raise ValueError("BRAIN_DB_KEY must be base64 of 32 bytes (Fernet key).")
    return raw.hex()


def get_connection():
    """Open a connection to the SQLite database. Create tables if they do not exist.

    If BRAIN_DB_KEY is set, the database is encrypted at rest using SQLCipher
    (AES-256 per-page encryption). No temp plaintext file is ever written.
    """
    key_hex = _get_key_hex()
    conn = sqlite3.connect(str(DB_PATH), timeout=30)
    conn.row_factory = sqlite3.Row

    if key_hex:
        conn.execute(f'PRAGMA key = "x\'{key_hex}\'"')  # hex only, no injection risk

    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 5000")   # wait for locks instead of erroring
    conn.execute("PRAGMA journal_mode = WAL")    # concurrent readers + one writer
    # Fail fast on wrong key
    conn.execute("SELECT count(*) FROM sqlite_master").fetchone()

    _create_tables(conn)
    _create_access_control_tables(conn)
    return conn



# =====================================================================
# Contact Operations
# =====================================================================

def upsert_contact(conn, name, email=None, phone=None, company=None, role=None,
                   source='manual', context_notes=None):
    """Insert or update a contact. Returns contact_id."""
    cur = conn.cursor()
    # Search for existing contact by email or name
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
        # Catat provenance untuk kontak baru
        add_provenance(conn, 'contact', contact_id, source,
                       source_detail=f'created: {name}', confidence=1.0)

    conn.commit()
    return contact_id


def get_contact(conn, contact_id):
    cur = conn.cursor()
    cur.execute("SELECT * FROM contacts WHERE id = ?", (contact_id,))
    return cur.fetchone()


def search_contacts(conn, query, limit=20):
    """Search contacts by name, email, or notes."""
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
    """Mark last interaction with this contact."""
    now = datetime.utcnow().isoformat()
    cur = conn.cursor()
    cur.execute("UPDATE contacts SET last_interaction = ?, updated_at = ? WHERE id = ?",
                (now, now, contact_id))
    conn.commit()


# =====================================================================
# Email Operations
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
    """Search emails by subject, body, sender, or recipient."""
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
# Calendar Operations
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
    """Retrieve upcoming calendar events."""
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
# Tweet Operations
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
# Notes Operations
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
    """Search notes by title, content, or tags."""
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
# Knowledge Entries Operations
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
    entry_id = cur.lastrowid
    # Catat provenance untuk entri pengetahuan baru
    add_provenance(conn, 'knowledge', entry_id, source or 'manual',
                   source_detail=f'created: {title}', confidence=1.0)
    conn.commit()
    return entry_id


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
# Conversations Operations
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
# Global Search (across all tables)
# =====================================================================

def global_search(conn, query, limit=10):
    """Search across contacts, emails, calendar, tweets, notes, and knowledge."""
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


# =====================================================================
# Embeddings for Semantic Search (real sentence-transformers embeddings)
# =====================================================================

def store_embedding(conn, source_type, source_id, content_chunk):
    """Compute and store a real embedding for a content chunk.

    Uses sentence-transformers/all-MiniLM-L6-v2 (384-dim).
    Falls back to TF-IDF if model unavailable.
    """
    vector = embed_text(content_chunk)

    cur = conn.cursor()
    cur.execute(
        "INSERT INTO embeddings (source_type, source_id, content_chunk, embedding_json) "
        "VALUES (?, ?, ?, ?)",
        (source_type, source_id, content_chunk, json.dumps(vector))
    )
    conn.commit()
    return cur.lastrowid


def semantic_search(conn, query, limit=10):
    """Semantic search using real embeddings + cosine similarity.

    Returns list of dicts with source_type, source_id, content_chunk, similarity.
    """
    query_vector = embed_text(query)

    cur = conn.cursor()
    cur.execute(
        "SELECT id, source_type, source_id, content_chunk, embedding_json FROM embeddings"
    )
    rows = cur.fetchall()

    if not rows:
        return []

    results = []
    for row in rows:
        stored_vec = json.loads(row[4]) if row[4] else []
        if not stored_vec:
            continue
        sim = _cosine_similarity(query_vector, stored_vec)
        if sim > 0.1:  # threshold for real embeddings
            results.append({
                "id": row[0],
                "source_type": row[1],
                "source_id": row[2],
                "content_chunk": row[3],
                "similarity": round(sim, 4),
            })

    results.sort(key=lambda x: x["similarity"], reverse=True)
    return results[:limit]


# =====================================================================
# Entity Operations (Knowledge Graph Nodes)
# =====================================================================

def upsert_entity(conn, name, entity_type='person', source='manual', context_notes=None):
    """Insert or update an entity. Returns entity_id."""
    cur = conn.cursor()
    cur.execute("SELECT id FROM entities WHERE name = ? AND entity_type = ?", (name, entity_type))
    row = cur.fetchone()
    now = datetime.utcnow().isoformat()

    if row:
        entity_id = row['id']
        cur.execute("""
            UPDATE entities
            SET source = COALESCE(?, source),
                context_notes = COALESCE(?, context_notes),
                updated_at = ?
            WHERE id = ?
        """, (source, context_notes, now, entity_id))
    else:
        cur.execute("""
            INSERT INTO entities (name, entity_type, source, context_notes)
            VALUES (?, ?, ?, ?)
        """, (name, entity_type, source, context_notes))
        entity_id = cur.lastrowid

    conn.commit()
    return entity_id


def get_entity(conn, entity_id):
    cur = conn.cursor()
    cur.execute("SELECT * FROM entities WHERE id = ?", (entity_id,))
    return cur.fetchone()


def search_entities(conn, query, limit=20):
    """Search entities by name or context notes."""
    cur = conn.cursor()
    like = f"%{query}%"
    cur.execute("""
        SELECT * FROM entities
        WHERE name LIKE ? OR context_notes LIKE ?
        ORDER BY name
        LIMIT ?
    """, (like, like, limit))
    return cur.fetchall()


# =====================================================================
# Entity Relation Operations (Knowledge Graph Edges)
# =====================================================================

def add_entity_relation(conn, source_entity_id, target_entity_id, relation_type,
                        source='manual', confidence=1.0):
    """Add a relation edge between two entities. Returns relation_id."""
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO entity_relations
        (source_entity_id, target_entity_id, relation_type, source, confidence)
        VALUES (?, ?, ?, ?, ?)
    """, (source_entity_id, target_entity_id, relation_type, source, confidence))
    relation_id = cur.lastrowid
    # Catat provenance untuk relasi baru
    add_provenance(conn, 'entity_relation', relation_id, source,
                   source_detail=f'{relation_type}: {source_entity_id} -> {target_entity_id}',
                   confidence=confidence)
    conn.commit()
    return relation_id


def get_entity_relations(conn, entity_id, limit=20):
    """Get all relations for an entity (both directions)."""
    cur = conn.cursor()
    cur.execute("""
        SELECT er.*, s.name AS source_name, t.name AS target_name
        FROM entity_relations er
        JOIN entities s ON er.source_entity_id = s.id
        JOIN entities t ON er.target_entity_id = t.id
        WHERE er.source_entity_id = ? OR er.target_entity_id = ?
        ORDER BY er.created_at DESC
        LIMIT ?
    """, (entity_id, entity_id, limit))
    return cur.fetchall()


def find_entity_path(conn, source_entity_id, target_entity_id, max_depth=3):
    """Breadth-first search for a path between two entities in the knowledge graph."""
    from collections import deque

    # Build adjacency list
    cur = conn.cursor()
    cur.execute("SELECT source_entity_id, target_entity_id, relation_type FROM entity_relations")
    adj = {}
    for row in cur.fetchall():
        adj.setdefault(row[0], []).append((row[1], row[2]))
        adj.setdefault(row[1], []).append((row[0], row[2]))  # undirected for path finding

    # BFS
    queue = deque([(source_entity_id, [])])
    visited = {source_entity_id}

    while queue:
        current, path = queue.popleft()
        if current == target_entity_id:
            return path
        if len(path) >= max_depth:
            continue
        for neighbor, rel_type in adj.get(current, []):
            if neighbor not in visited:
                visited.add(neighbor)
                queue.append((neighbor, path + [(current, neighbor, rel_type)]))
    return None


# =====================================================================
# Knowledge Graph Query
# =====================================================================

def query_knowledge_graph(conn, query, limit=10):
    """Find entities and their relations relevant to a query.

    Returns dict with 'entities' and 'relations'.
    """
    entities = search_entities(conn, query, limit=limit)
    result_entities = [dict(r) for r in entities]

    # Get relations for found entities
    entity_ids = [e['id'] for e in result_entities]
    relations = []
    for eid in entity_ids:
        rels = get_entity_relations(conn, eid, limit=limit)
        for r in rels:
            relations.append({
                "source_entity_id": r['source_entity_id'],
                "source_name": r['source_name'],
                "target_entity_id": r['target_entity_id'],
                "target_name": r['target_name'],
                "relation_type": r['relation_type'],
                "source": r['source'],
                "confidence": r['confidence'],
            })

    # Deduplicate relations
    seen = set()
    unique_relations = []
    for r in relations:
        key = (r['source_entity_id'], r['target_entity_id'], r['relation_type'])
        if key not in seen:
            seen.add(key)
            unique_relations.append(r)

    return {
        "entities": result_entities,
        "relations": unique_relations[:limit],
    }


# =====================================================================
# Edit / Delete Operations (Koreksi dan Penarikan Fakta)
# =====================================================================

def update_contact(conn, contact_id, name=None, email=None, phone=None,
                   company=None, role=None, context_notes=None, source=None):
    """Update an existing contact. Only provided fields are changed."""
    cur = conn.cursor()
    cur.execute("SELECT id FROM contacts WHERE id = ?", (contact_id,))
    if not cur.fetchone():
        return None
    now = datetime.utcnow().isoformat()
    fields = []
    values = []
    if name is not None:
        fields.append("name = ?"); values.append(name)
    if email is not None:
        fields.append("email = ?"); values.append(email)
    if phone is not None:
        fields.append("phone = ?"); values.append(phone)
    if company is not None:
        fields.append("company = ?"); values.append(company)
    if role is not None:
        fields.append("role = ?"); values.append(role)
    if context_notes is not None:
        fields.append("context_notes = ?"); values.append(context_notes)
    if source is not None:
        fields.append("source = ?"); values.append(source)
    fields.append("updated_at = ?"); values.append(now)
    values.append(contact_id)
    cur.execute(f"UPDATE contacts SET {', '.join(fields)} WHERE id = ?", values)
    conn.commit()
    return contact_id


def delete_contact(conn, contact_id):
    """Delete a contact and its related emails."""
    cur = conn.cursor()
    cur.execute("DELETE FROM emails WHERE contact_id = ?", (contact_id,))
    cur.execute("DELETE FROM contacts WHERE id = ?", (contact_id,))
    conn.commit()
    return cur.rowcount > 0


def update_note(conn, note_id, title=None, content=None, summary=None,
                tags=None, last_modified=None):
    """Update an existing note."""
    cur = conn.cursor()
    cur.execute("SELECT id FROM notes WHERE id = ?", (note_id,))
    if not cur.fetchone():
        return None
    now = datetime.utcnow().isoformat()
    fields = []
    values = []
    if title is not None:
        fields.append("title = ?"); values.append(title)
    if content is not None:
        fields.append("content = ?"); values.append(content)
    if summary is not None:
        fields.append("summary = ?"); values.append(summary)
    if tags is not None:
        fields.append("tags = ?"); values.append(json.dumps(tags))
    if last_modified is not None:
        fields.append("last_modified = ?"); values.append(last_modified)
    fields.append("updated_at = ?"); values.append(now)
    values.append(note_id)
    cur.execute(f"UPDATE notes SET {', '.join(fields)} WHERE id = ?", values)
    conn.commit()
    return note_id


def delete_note(conn, note_id):
    """Delete a note."""
    cur = conn.cursor()
    cur.execute("DELETE FROM notes WHERE id = ?", (note_id,))
    conn.commit()
    return cur.rowcount > 0


def update_knowledge_entry(conn, entry_id, entry_type=None, title=None,
                           content=None, related_contact_ids=None, source=None):
    """Update an existing knowledge entry."""
    cur = conn.cursor()
    cur.execute("SELECT id FROM knowledge_entries WHERE id = ?", (entry_id,))
    if not cur.fetchone():
        return None
    now = datetime.utcnow().isoformat()
    fields = []
    values = []
    if entry_type is not None:
        fields.append("entry_type = ?"); values.append(entry_type)
    if title is not None:
        fields.append("title = ?"); values.append(title)
    if content is not None:
        fields.append("content = ?"); values.append(content)
    if related_contact_ids is not None:
        fields.append("related_contact_ids = ?"); values.append(json.dumps(related_contact_ids))
    if source is not None:
        fields.append("source = ?"); values.append(source)
    fields.append("updated_at = ?"); values.append(now)
    values.append(entry_id)
    cur.execute(f"UPDATE knowledge_entries SET {', '.join(fields)} WHERE id = ?", values)
    conn.commit()
    return entry_id


def delete_knowledge_entry(conn, entry_id):
    """Delete a knowledge entry."""
    cur = conn.cursor()
    cur.execute("DELETE FROM knowledge_entries WHERE id = ?", (entry_id,))
    conn.commit()
    return cur.rowcount > 0


def delete_entity_relation(conn, relation_id):
    """Delete a knowledge graph relation."""
    cur = conn.cursor()
    cur.execute("DELETE FROM entity_relations WHERE id = ?", (relation_id,))
    conn.commit()
    return cur.rowcount > 0


def delete_entity(conn, entity_id):
    """Delete an entity and all its relations."""
    cur = conn.cursor()
    cur.execute("DELETE FROM entity_relations WHERE source_entity_id = ? OR target_entity_id = ?",
                (entity_id, entity_id))
    cur.execute("DELETE FROM entities WHERE id = ?", (entity_id,))
    conn.commit()
    return cur.rowcount > 0


# =====================================================================
# Export / Import
# =====================================================================

def export_brain_json(conn):
    """Export all brain data to a JSON-serializable dict."""
    cur = conn.cursor()
    data = {}

    def fetch_all(table, order_by=None):
        q = f"SELECT * FROM {table}"
        if order_by:
            q += f" ORDER BY {order_by}"
        cur.execute(q)
        return [dict(r) for r in cur.fetchall()]

    data["contacts"] = fetch_all("contacts", "id")
    data["emails"] = fetch_all("emails", "id")
    data["calendar_events"] = fetch_all("calendar_events", "id")
    data["tweets"] = fetch_all("tweets", "id")
    data["notes"] = fetch_all("notes", "id")
    data["knowledge_entries"] = fetch_all("knowledge_entries", "id")
    data["conversations"] = fetch_all("conversations", "id")
    data["entities"] = fetch_all("entities", "id")
    data["entity_relations"] = fetch_all("entity_relations", "id")
    data["embeddings"] = fetch_all("embeddings", "id")
    data["exported_at"] = datetime.utcnow().isoformat()
    return data


def export_brain_markdown(conn):
    """Export brain data as markdown text."""
    lines = ["# Personal Brain Export", ""]
    lines.append(f"Exported at: {datetime.utcnow().isoformat()}")
    lines.append("")

    # Contacts
    contacts = [dict(r) for r in search_contacts(conn, "", limit=10000)]
    if contacts:
        lines.append("## Contacts")
        lines.append("")
        for c in contacts:
            lines.append(f"### {c['name']}")
            if c.get('email'): lines.append(f"- Email: {c['email']}")
            if c.get('phone'): lines.append(f"- Phone: {c['phone']}")
            if c.get('company'): lines.append(f"- Company: {c['company']}")
            if c.get('role'): lines.append(f"- Role: {c['role']}")
            if c.get('context_notes'): lines.append(f"- Notes: {c['context_notes']}")
            lines.append("")

    # Notes
    notes = [dict(r) for r in search_notes(conn, "", limit=10000)]
    if notes:
        lines.append("## Notes")
        lines.append("")
        for n in notes:
            lines.append(f"### {n.get('title', n.get('filename', '(no title)'))}")
            if n.get('filename'): lines.append(f"- File: {n['filename']}")
            if n.get('summary'): lines.append(f"- Summary: {n['summary']}")
            lines.append(n.get('content', ''))
            lines.append("")

    # Knowledge
    knowledge = [dict(r) for r in search_knowledge(conn, "", limit=10000)]
    if knowledge:
        lines.append("## Knowledge Entries")
        lines.append("")
        for k in knowledge:
            lines.append(f"### {k.get('title', '(no title)')}")
            lines.append(f"- Type: {k.get('entry_type', 'unknown')}")
            lines.append(f"- Source: {k.get('source', 'unknown')}")
            lines.append(k.get('content', ''))
            lines.append("")

    # Entity relations
    cur = conn.cursor()
    cur.execute("""
        SELECT er.*, s.name AS source_name, t.name AS target_name
        FROM entity_relations er
        JOIN entities s ON er.source_entity_id = s.id
        JOIN entities t ON er.target_entity_id = t.id
    """)
    relations = cur.fetchall()
    if relations:
        lines.append("## Knowledge Graph Relations")
        lines.append("")
        for r in relations:
            lines.append(f"- {r['source_name']} →{r['relation_type']}→ {r['target_name']}")
        lines.append("")

    return "\n".join(lines)


def import_brain_json(conn, data):
    """Import brain data from a JSON dict. Skips duplicates."""
    imported = {"contacts": 0, "notes": 0, "knowledge_entries": 0, "entities": 0, "entity_relations": 0}

    for c in data.get("contacts", []):
        try:
            upsert_contact(
                conn, name=c.get("name", ""), email=c.get("email"),
                phone=c.get("phone"), company=c.get("company"),
                role=c.get("role"), source=c.get("source", "import"),
                context_notes=c.get("context_notes"),
            )
            imported["contacts"] += 1
        except Exception:
            pass

    for n in data.get("notes", []):
        try:
            upsert_note(
                conn, filename=n.get("filename", "imported.md"),
                file_path=n.get("file_path", ""),
                title=n.get("title", ""), content=n.get("content", ""),
                summary=n.get("summary"), tags=n.get("tags"),
                last_modified=n.get("last_modified"),
            )
            imported["notes"] += 1
        except Exception:
            pass

    for k in data.get("knowledge_entries", []):
        try:
            insert_knowledge_entry(
                conn, entry_type=k.get("entry_type", "import"),
                title=k.get("title", ""), content=k.get("content", ""),
                related_contact_ids=k.get("related_contact_ids"),
                source=k.get("source", "import"),
            )
            imported["knowledge_entries"] += 1
        except Exception:
            pass

    for e in data.get("entities", []):
        try:
            upsert_entity(
                conn, name=e.get("name", ""), entity_type=e.get("entity_type", "person"),
                source=e.get("source", "import"), context_notes=e.get("context_notes"),
            )
            imported["entities"] += 1
        except Exception:
            pass

    for r in data.get("entity_relations", []):
        try:
            # Resolve names to IDs
            src = search_entities(conn, r.get("source_name", ""), limit=1)
            tgt = search_entities(conn, r.get("target_name", ""), limit=1)
            if src and tgt:
                add_entity_relation(
                    conn, src[0]["id"], tgt[0]["id"],
                    r.get("relation_type", "related"),
                    source=r.get("source", "import"),
                    confidence=r.get("confidence", 1.0),
                )
                imported["entity_relations"] += 1
        except Exception:
            pass

    conn.commit()
    return imported


# =====================================================================
# Provenance & Edit/Retraction (Fase 3b)
# =====================================================================

def _ensure_provenance_columns(conn):
    """Add status/correction columns to existing tables if missing (idempotent)."""
    cur = conn.cursor()

    # contacts
    cols = {row[1] for row in cur.execute("PRAGMA table_info(contacts)").fetchall()}
    for col, defn in [
        ("status", "TEXT DEFAULT 'active'"),
        ("corrected_at", "TEXT"),
        ("corrected_by", "TEXT"),
        ("original_name", "TEXT"),
        ("original_email", "TEXT"),
    ]:
        if col not in cols:
            cur.execute(f"ALTER TABLE contacts ADD COLUMN {col} {defn}")

    # knowledge_entries
    cols = {row[1] for row in cur.execute("PRAGMA table_info(knowledge_entries)").fetchall()}
    for col, defn in [
        ("status", "TEXT DEFAULT 'active'"),
        ("corrected_at", "TEXT"),
        ("corrected_by", "TEXT"),
        ("original_content", "TEXT"),
    ]:
        if col not in cols:
            cur.execute(f"ALTER TABLE knowledge_entries ADD COLUMN {col} {defn}")

    # entity_relations
    cols = {row[1] for row in cur.execute("PRAGMA table_info(entity_relations)").fetchall()}
    for col, defn in [
        ("status", "TEXT DEFAULT 'active'"),
        ("corrected_at", "TEXT"),
        ("corrected_by", "TEXT"),
    ]:
        if col not in cols:
            cur.execute(f"ALTER TABLE entity_relations ADD COLUMN {col} {defn}")

    conn.commit()


def _create_tables(conn):
    """Initialize all brain tables."""
    cur = conn.cursor()

    # Contacts of people known to the agent
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
            status TEXT DEFAULT 'active',   -- active, corrected, retracted
            corrected_at TEXT,
            corrected_by TEXT,
            original_name TEXT,
            original_email TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
        )
    """)

    # Emails from Gmail
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
            contact_id INTEGER,    -- FK to contacts
            created_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (contact_id) REFERENCES contacts(id)
        )
    """)

    # Calendar events from Google Calendar
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
            contact_id INTEGER,    -- FK to contacts (organizer atau primary attendee)
            source TEXT DEFAULT 'google_calendar',
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (contact_id) REFERENCES contacts(id)
        )
    """)

    # Tweets from Twitter/X
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

    # Markdown notes from local folder
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

    # Agent conversation history
    cur.execute("""
        CREATE TABLE IF NOT EXISTS conversations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_message TEXT,
            agent_response TEXT,
            context_used TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        )
    """)

    # Free-form knowledge entries
    cur.execute("""
        CREATE TABLE IF NOT EXISTS knowledge_entries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            entry_type TEXT,
            title TEXT,
            content TEXT,
            related_contact_ids TEXT,
            source TEXT,
            status TEXT DEFAULT 'active',
            corrected_at TEXT,
            corrected_by TEXT,
            original_name TEXT,
            original_email TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
        )
    """)

    # Entities for knowledge graph
    cur.execute("""
        CREATE TABLE IF NOT EXISTS entities (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            entity_type TEXT DEFAULT 'person',
            source TEXT DEFAULT 'manual',
            context_notes TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
        )
    """)

    # Entity relations (knowledge graph edges)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS entity_relations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_entity_id INTEGER,
            target_entity_id INTEGER,
            relation_type TEXT,
            source TEXT DEFAULT 'manual',
            confidence REAL DEFAULT 1.0,
            status TEXT DEFAULT 'active',
            corrected_at TEXT,
            corrected_by TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (source_entity_id) REFERENCES entities(id),
            FOREIGN KEY (target_entity_id) REFERENCES entities(id)
        )
    """)

    # Provenance entries
    cur.execute("""
        CREATE TABLE IF NOT EXISTS provenance_entries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            subject_type TEXT NOT NULL,
            subject_id INTEGER NOT NULL,
            source_type TEXT NOT NULL,
            source_detail TEXT,
            confidence REAL DEFAULT 1.0,
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
        )
    """)

    # Embeddings for semantic search (content chunks with TF-IDF vectors)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS embeddings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_type TEXT,
            source_id INTEGER,
            content_chunk TEXT,
            embedding_json TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        )
    """)

    conn.commit()
    _ensure_item_label_columns(conn)
    _ensure_provenance_columns(conn)


def _ensure_item_label_columns(conn):
    """Add owner_user_id, provenance_label, injection_risk columns if missing.

    SQLite has no ADD COLUMN IF NOT EXISTS, so we inspect PRAGMA table_info
    and only add columns that are absent. Safe to run on every connection.
    """
    cur = conn.cursor()

    for table in ("contacts", "notes"):
        cols = {row[1] for row in cur.execute(f"PRAGMA table_info({table})")}
        if "owner_user_id" not in cols:
            cur.execute(f"ALTER TABLE {table} ADD COLUMN owner_user_id INTEGER")
        if "provenance_label" not in cols:
            cur.execute(f"ALTER TABLE {table} ADD COLUMN provenance_label TEXT")
        if "injection_risk" not in cols:
            cur.execute(f"ALTER TABLE {table} ADD COLUMN injection_risk TEXT DEFAULT 'none'")
    conn.commit()


# ----------------------------------------------------------------------
# Prompt-injection detection + item labeling
# ----------------------------------------------------------------------


_INJECTION_PATTERNS = [
    r"ignore\s+previous\s+instructions",
    r"ignore\s+all\s+prior\s+instructions",
    r"disregard\s+previous\s+instructions",
    r"system\s*:\s*",
    r"you\s+are\s+now\s+",
    r"jailbreak",
    r"prompt\s*injection",
    r"override\s+safety",
    r"bypass\s+filter",
]


def detect_prompt_injection(text):
    """Deteksi potensi prompt-injection dalam teks input.

    Returns (is_injection: bool, risk_level: str) where risk_level is
    'none', 'low', 'medium', or 'high'.
    """
    if not text:
        return False, "none"
    lowered = text.lower()
    hits = 0
    for pattern in _INJECTION_PATTERNS:
        if re.search(pattern, lowered):
            hits += 1
    if hits == 0:
        return False, "none"
    if hits >= 3:
        return True, "high"
    if hits == 2:
        return True, "medium"
    return True, "low"


def label_item(conn, item_type, item_id, provenance_label=None, injection_risk=None):
    """Simpan label provenance dan risiko injection pada item.

    item_type: 'contact' atau 'note'
    """
    table = "contacts" if item_type == "contact" else "notes"
    cur = conn.cursor()
    cur.execute(
        f"UPDATE {table} SET provenance_label = ?, injection_risk = ? WHERE id = ?",
        (provenance_label, injection_risk, item_id),
    )
    conn.commit()
    return cur.rowcount > 0


# ---------- Owner-scoped queries ----------

def get_contacts_by_owner(conn, user_id, limit=100):
    """Daftar kontak milik user (owner_user_id = user_id)."""
    cur = conn.cursor()
    cur.execute(
        "SELECT * FROM contacts WHERE owner_user_id = ? ORDER BY updated_at DESC LIMIT ?",
        (user_id, limit),
    )
    return cur.fetchall()


def get_notes_by_owner(conn, user_id, limit=100):
    """Daftar note milik user (owner_user_id = user_id)."""
    cur = conn.cursor()
    cur.execute(
        "SELECT * FROM notes WHERE owner_user_id = ? ORDER BY updated_at DESC LIMIT ?",
        (user_id, limit),
    )
    return cur.fetchall()

# =====================================================================
# Provenance Operations
# =====================================================================

def add_provenance(conn, subject_type, subject_id, source_type,
                   source_detail=None, confidence=1.0):
    """Catat sumber asal suatu fakta (provenance trail).

    Args:
        subject_type: 'contact', 'knowledge', atau 'entity_relation'
        subject_id: ID baris di tabel subjek
        source_type: 'gmail', 'calendar', 'twitter', 'conversation', 'manual'
        source_detail: detail sumber (message_id, event_id, tweet_id, dll.)
        confidence: skor kepercayaan 0.0-1.0
    """
    cur = conn.cursor()
    now = datetime.utcnow().isoformat()
    cur.execute("""
        INSERT INTO provenance_entries
        (subject_type, subject_id, source_type, source_detail,
         confidence, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (subject_type, subject_id, source_type, source_detail,
          confidence, now, now))
    conn.commit()
    return cur.lastrowid


def get_provenance(conn, subject_type, subject_id):
    """Ambil semua entri provenance untuk subjek tertentu."""
    cur = conn.cursor()
    cur.execute("""
        SELECT * FROM provenance_entries
        WHERE subject_type = ? AND subject_id = ?
        ORDER BY created_at DESC
    """, (subject_type, subject_id))
    return [dict(r) for r in cur.fetchall()]


# =====================================================================
# Edit / Retraction Operations
# =====================================================================

def retract_contact(conn, contact_id, reason, retracted_by):
    """Tarik kembali kontak (tandai sebagai retracted, tidak menghapus data)."""
    cur = conn.cursor()
    cur.execute("SELECT id, name, email FROM contacts WHERE id = ?", (contact_id,))
    row = cur.fetchone()
    if not row:
        return None
    now = datetime.utcnow().isoformat()
    cur.execute("""
        UPDATE contacts
        SET status = 'retracted',
            corrected_at = ?,
            corrected_by = ?,
            original_name = name,
            original_email = email,
            updated_at = ?
        WHERE id = ?
    """, (now, retracted_by, now, contact_id))
    # Catat provenance penarikan
    cur.execute("""
        INSERT INTO provenance_entries
        (subject_type, subject_id, source_type, source_detail,
         confidence, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, ('contact', contact_id, 'manual',
          f'retracted: {reason}', 1.0, now, now))
    conn.commit()
    return contact_id


def retract_knowledge_entry(conn, entry_id, reason, retracted_by):
    """Tarik kembali entri pengetahuan."""
    cur = conn.cursor()
    cur.execute("SELECT id, content FROM knowledge_entries WHERE id = ?", (entry_id,))
    row = cur.fetchone()
    if not row:
        return None
    now = datetime.utcnow().isoformat()
    cur.execute("""
        UPDATE knowledge_entries
        SET status = 'retracted',
            corrected_at = ?,
            corrected_by = ?,
            original_content = content,
            updated_at = ?
        WHERE id = ?
    """, (now, retracted_by, now, entry_id))
    cur.execute("""
        INSERT INTO provenance_entries
        (subject_type, subject_id, source_type, source_detail,
         confidence, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, ('knowledge', entry_id, 'manual',
          f'retracted: {reason}', 1.0, now, now))
    conn.commit()
    return entry_id


def correct_knowledge_entry(conn, entry_id, new_content, corrected_by, reason):
    """Perbaiki isi entri pengetahuan, simpan versi asli sebagai original_content."""
    cur = conn.cursor()
    cur.execute("SELECT id, content FROM knowledge_entries WHERE id = ?", (entry_id,))
    row = cur.fetchone()
    if not row:
        return None
    now = datetime.utcnow().isoformat()
    cur.execute("""
        UPDATE knowledge_entries
        SET content = ?,
            status = 'corrected',
            corrected_at = ?,
            corrected_by = ?,
            original_content = content,
            updated_at = ?
        WHERE id = ?
    """, (new_content, now, corrected_by, now, entry_id))
    cur.execute("""
        INSERT INTO provenance_entries
        (subject_type, subject_id, source_type, source_detail,
         confidence, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, ('knowledge', entry_id, 'manual',
          f'corrected: {reason}', 1.0, now, now))
    conn.commit()
    return entry_id


# =====================================================================
# Memory Consolidation (Fase 3b)
# =====================================================================

def consolidate_memory(conn, max_conversation_age_days=30,
                       similarity_threshold=0.85):
    """Konsolidasi memori:
    1. Deduplikasi kontak yang sama (berdasarkan email atau nama).
    2. Ringkas percakapan lama (> max_conversation_age_days hari)
       menjadi entri knowledge.
    3. Tandai entri knowledge lama (> 90 hari) untuk tinjauan ulang.

    Returns dict dengan statistik tindakan.
    """
    from datetime import datetime, timedelta
    stats = {
        "contacts_deduplicated": 0,
        "conversations_summarized": 0,
        "knowledge_flagged": 0,
    }

    now = datetime.utcnow()
    cutoff = (now - timedelta(days=max_conversation_age_days)).isoformat()

    # --- 1. Deduplikasi kontak berdasarkan email ---
    cur = conn.cursor()
    cur.execute("""
        SELECT id, name, email, created_at
        FROM contacts
        WHERE email IS NOT NULL AND email != ''
        ORDER BY email, created_at
    """)
    rows = cur.fetchall()
    email_groups = {}
    for r in rows:
        email = r['email']
        if email not in email_groups:
            email_groups[email] = []
        email_groups[email].append(dict(r))

    for email, group in email_groups.items():
        if len(group) <= 1:
            continue
        # Pertahankan yang paling baru, tarik kembali yang lama
        group.sort(key=lambda r: r['created_at'] or '', reverse=True)
        keeper = group[0]
        for dup in group[1:]:
            retract_contact(conn, dup['id'],
                            f"Duplikat email {email}, digabungkan ke ID {keeper['id']}",
                            "system_consolidation")
            stats["contacts_deduplicated"] += 1

    # --- 2. Ringkas percakapan lama menjadi entri knowledge ---
    cur.execute("""
        SELECT id, user_message, agent_response, created_at
        FROM conversations
        WHERE created_at < ?
        ORDER BY created_at
    """, (cutoff,))
    old_convs = cur.fetchall()
    for conv in old_convs:
        # Buat entri knowledge dari percakapan lama
        title = f"Ringkasan percakapan: {conv['user_message'][:50]}"
        content = f"User: {conv['user_message']}\nAgen: {conv['agent_response']}"
        insert_knowledge_entry(
            conn,
            entry_type="conversation_summary",
            title=title,
            content=content,
            source="consolidation",
        )
        stats["conversations_summarized"] += 1

    # --- 3. Tandai entri knowledge lama (> 90 hari) untuk tinjauan ---
    review_cutoff = (now - timedelta(days=90)).isoformat()
    cur.execute("""
        SELECT id FROM knowledge_entries
        WHERE status = 'active' AND created_at < ?
    """, (review_cutoff,))
    old_knowledge = cur.fetchall()
    for k in old_knowledge:
        # Tandai perlu ditinjau (hanya catatan provenance, status tetap active)
        cur.execute("""
            INSERT INTO provenance_entries
            (subject_type, subject_id, source_type, source_detail,
             confidence, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, ('knowledge', k['id'], 'system_consolidation',
              'flagged_for_review: older than 90 days', 0.5,
              now, now))
        stats["knowledge_flagged"] += 1

    conn.commit()
    return stats


# =====================================================================
# Users & Access Control (Sharing)
# =====================================================================

def _ensure_access_control_columns(conn):
    """Add owner_user_id to existing tables if missing (idempotent)."""
    cur = conn.cursor()

    # contacts
    cols = {row[1] for row in cur.execute("PRAGMA table_info(contacts)").fetchall()}
    if "owner_user_id" not in cols:
        cur.execute("ALTER TABLE contacts ADD COLUMN owner_user_id TEXT")

    # knowledge_entries
    cols = {row[1] for row in cur.execute("PRAGMA table_info(knowledge_entries)").fetchall()}
    if "owner_user_id" not in cols:
        cur.execute("ALTER TABLE knowledge_entries ADD COLUMN owner_user_id TEXT")

    # entity_relations
    cols = {row[1] for row in cur.execute("PRAGMA table_info(entity_relations)").fetchall()}
    if "owner_user_id" not in cols:
        cur.execute("ALTER TABLE entity_relations ADD COLUMN owner_user_id TEXT")

    # notes
    cols = {row[1] for row in cur.execute("PRAGMA table_info(notes)").fetchall()}
    if "owner_user_id" not in cols:
        cur.execute("ALTER TABLE notes ADD COLUMN owner_user_id TEXT")

    conn.commit()


def _create_access_control_tables(conn):
    """Create users and item_shares tables."""
    cur = conn.cursor()

    # Users
    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id TEXT PRIMARY KEY,
            username TEXT UNIQUE NOT NULL,
            email TEXT,
            password_hash TEXT,
            role TEXT DEFAULT 'user',  -- 'user', 'admin'
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
        )
    """)

    # Item shares — who can access which item
    cur.execute("""
        CREATE TABLE IF NOT EXISTS item_shares (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            item_type TEXT NOT NULL,   -- 'contact', 'knowledge', 'entity_relation', 'note'
            item_id INTEGER NOT NULL,
            owner_user_id TEXT NOT NULL,
            shared_with_user_id TEXT NOT NULL,
            access_level TEXT DEFAULT 'read',  -- 'read', 'write', 'admin'
            created_at TEXT DEFAULT (datetime('now')),
            UNIQUE(item_type, item_id, shared_with_user_id)
        )
    """)

    conn.commit()
    _ensure_access_control_columns(conn)


def create_user(conn, user_id, username, email=None, password_hash=None, role='user'):
    """Create a new user."""
    cur = conn.cursor()
    now = datetime.utcnow().isoformat()
    cur.execute("""
        INSERT OR REPLACE INTO users
        (id, username, email, password_hash, role, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (user_id, username, email, password_hash, role, now, now))
    conn.commit()
    return user_id


def get_user(conn, user_id):
    """Get user by ID."""
    cur = conn.cursor()
    cur.execute("SELECT * FROM users WHERE id = ?", (user_id,))
    row = cur.fetchone()
    return dict(row) if row else None


def get_user_by_username(conn, username):
    """Get user by username."""
    cur = conn.cursor()
    cur.execute("SELECT * FROM users WHERE username = ?", (username,))
    row = cur.fetchone()
    return dict(row) if row else None


def share_item(conn, item_type, item_id, owner_user_id, shared_with_user_id, access_level='read'):
    """Share an item with another user."""
    cur = conn.cursor()
    now = datetime.utcnow().isoformat()
    cur.execute("""
        INSERT OR REPLACE INTO item_shares
        (item_type, item_id, owner_user_id, shared_with_user_id, access_level, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (item_type, item_id, owner_user_id, shared_with_user_id, access_level, now))
    conn.commit()
    return cur.lastrowid


def unshare_item(conn, item_type, item_id, shared_with_user_id):
    """Remove sharing of an item with a user."""
    cur = conn.cursor()
    cur.execute("""
        DELETE FROM item_shares
        WHERE item_type = ? AND item_id = ? AND shared_with_user_id = ?
    """, (item_type, item_id, shared_with_user_id))
    conn.commit()
    return cur.rowcount > 0


def get_accessible_items(conn, user_id, item_type, limit=1000):
    """Get items accessible to a user (owned + shared).

    Returns list of dicts with item data plus 'access_level' field.
    """
    cur = conn.cursor()

    # Map item_type to table name
    table_map = {
        'contact': 'contacts',
        'knowledge': 'knowledge_entries',
        'entity_relation': 'entity_relations',
        'note': 'notes',
    }
    table = table_map.get(item_type)
    if not table:
        return []

    # Owned items
    cur.execute(f"""
        SELECT *, 'owner' as access_level
        FROM {table}
        WHERE owner_user_id = ?
    """, (user_id,))
    owned = [dict(r) for r in cur.fetchall()]

    # Shared items
    cur.execute("""
        SELECT s.item_id, s.access_level
        FROM item_shares s
        WHERE s.item_type = ? AND s.shared_with_user_id = ?
    """, (item_type, user_id))
    shared_rows = cur.fetchall()

    shared_items = []
    for sr in shared_rows:
        access_level = sr['access_level']
        cur.execute(f"SELECT * FROM {table} WHERE id = ?", (sr['item_id'],))
        row = cur.fetchone()
        if row:
            item = dict(row)
            item['access_level'] = access_level
            shared_items.append(item)

    # Combine, dedupe by id
    all_items = {}
    for item in owned + shared_items:
        all_items[item['id']] = item

    return list(all_items.values())[:limit]


def check_item_access(conn, user_id, item_type, item_id, required_level='read'):
    """Check if user has access to an item.

    Returns access_level string if access granted, None if denied.
    """
    # Owner always has admin access
    table_map = {
        'contact': 'contacts',
        'knowledge': 'knowledge_entries',
        'entity_relation': 'entity_relations',
        'note': 'notes',
    }
    table = table_map.get(item_type)
    if not table:
        return None

    cur = conn.cursor()
    cur.execute(f"SELECT owner_user_id FROM {table} WHERE id = ?", (item_id,))
    row = cur.fetchone()
    if row and row['owner_user_id'] == user_id:
        return 'admin'

    # Check shares
    level_map = {'read': 1, 'write': 2, 'admin': 3}
    required_level_num = level_map.get(required_level, 1)

    cur.execute("""
        SELECT access_level FROM item_shares
        WHERE item_type = ? AND item_id = ? AND shared_with_user_id = ?
    """, (item_type, item_id, user_id))
    row = cur.fetchone()
    if row:
        user_level_num = level_map.get(row['access_level'], 1)
        if user_level_num >= required_level_num:
            return row['access_level']

    return None


def upsert_contact_with_owner(conn, name, email=None, phone=None, company=None, role=None,
                              source='manual', context_notes=None, owner_user_id=None):
    """Insert or update a contact with owner tracking."""
    cur = conn.cursor()
    now = datetime.utcnow().isoformat()
    # Search for existing contact by email or name
    cur.execute("SELECT id FROM contacts WHERE email = ? OR name = ?", (email, name))
    row = cur.fetchone()

    if row:
        contact_id = row['id']
        cur.execute("""
            UPDATE contacts
            SET phone = COALESCE(?, phone),
                company = COALESCE(?, company),
                role = COALESCE(?, role),
                source = COALESCE(?, source),
                context_notes = COALESCE(?, context_notes),
                owner_user_id = COALESCE(?, owner_user_id),
                updated_at = ?
            WHERE id = ?
        """, (phone, company, role, source, context_notes, owner_user_id, now, contact_id))
    else:
        cur.execute("""
            INSERT INTO contacts (name, email, phone, company, role, source,
                                  context_notes, last_interaction, owner_user_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (name, email, phone, company, role, source, context_notes, now, owner_user_id))
        contact_id = cur.lastrowid
        # Catat provenance untuk kontak baru
        add_provenance(conn, 'contact', contact_id, source,
                       source_detail=f'created: {name}', confidence=1.0)

    conn.commit()
    return contact_id


def upsert_note_with_owner(conn, filename, file_path, title, content, summary=None,
                           tags=None, last_modified=None, owner_user_id=None):
    """Insert or update a note with owner tracking."""
    cur = conn.cursor()
    tags_json = json.dumps(tags or [])
    now = datetime.utcnow().isoformat()
    cur.execute("SELECT id FROM notes WHERE file_path = ?", (file_path,))
    row = cur.fetchone()

    if row:
        note_id = row['id']
        cur.execute("""
            UPDATE notes
            SET title = COALESCE(?, title),
                content = COALESCE(?, content),
                summary = COALESCE(?, summary),
                tags = COALESCE(?, tags),
                last_modified = COALESCE(?, last_modified),
                owner_user_id = COALESCE(?, owner_user_id),
                updated_at = ?
            WHERE id = ?
        """, (title, content, summary, tags_json, last_modified, owner_user_id, now, note_id))
    else:
        cur.execute("""
            INSERT INTO notes
            (filename, file_path, title, content, summary, tags,
             last_modified, created_at, updated_at, owner_user_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (filename, file_path, title, content, summary, tags_json,
              last_modified, now, now, owner_user_id))
        note_id = cur.lastrowid

    conn.commit()
    return note_id


# =====================================================================
# Export Markdown with Backlinks (Fase 3g)
# =====================================================================

def _sanitize_filename(name):
    """Buat nama file yang aman dari nama entitas."""
    import re
    safe = re.sub(r'[<>:"/\\|?*]', '_', name.strip())
    safe = re.sub(r'\s+', '_', safe)
    return safe or 'untitled'


def _add_backlinks(text, entity_names):
    """Ganti nama entitas dalam teks dengan wiki-style backlinks [[Nama]].

    Hanya mengganti jika nama belum ada dalam konteks link [[...]] atau ( ... ).
    """
    import re

    # Urutkan nama berdasarkan panjang (terlama dulu) agar tidak partial match
    sorted_names = sorted(entity_names, key=len, reverse=True)

    for name in sorted_names:
        if not name:
            continue
        escaped = re.escape(name)
        pattern = r'(?<!\[\[)(?<!\[)' + escaped + r'(?!\])(?!\])'
        text = re.sub(pattern, f'[[{name}]]', text)

    return text


def export_brain_markdown_with_backlinks(conn, output_folder):
    """Export brain data sebagai file markdown terpisah dengan backlinks.

    Setiap kontak, entri knowledge, note, dan entitas mendapat file .md sendiri
    dengan frontmatter YAML. Referensi ke entitas lain dalam content diubah
    menjadi [[Nama]] wiki-style links.

    Struktur output:
        output_folder/
        ├── index.md              # Indeks semua entitas
        ├── graph.md              # Visualisasi relasi knowledge graph
        ├── contacts/
        │   ├── Alice Smith.md
        │   └── Bob Jones.md
        ├── knowledge/
        │   └── Project Alpha.md
        ├── notes/
        │   └── Meeting Notes.md
        └── entities/
            ├── Acme Corp.md
            └── Project Alpha.md

    Args:
        conn: Database connection
        output_folder: Folder tujuan export (akan dibuat jika belum ada)

    Returns:
        dict dengan statistik export per kategori
    """
    from pathlib import Path
    import json
    from datetime import datetime

    output = Path(output_folder)
    output.mkdir(parents=True, exist_ok=True)

    contacts_dir = output / "contacts"
    knowledge_dir = output / "knowledge"
    notes_dir = output / "notes"
    entities_dir = output / "entities"
    for d in [contacts_dir, knowledge_dir, notes_dir, entities_dir]:
        d.mkdir(parents=True, exist_ok=True)

    stats = {
        "contacts": 0,
        "knowledge": 0,
        "notes": 0,
        "entities": 0,
        "relations": 0,
    }

    cur = conn.cursor()

    # --- Kontak ---
    contacts = [dict(r) for r in search_contacts(conn, "", limit=10000)]
    contact_names = [c['name'] for c in contacts if c.get('name')]

    for c in contacts:
        name = c.get('name', '(no name)')
        filename = _sanitize_filename(name) + ".md"
        filepath = contacts_dir / filename

        lines = []
        lines.append("---")
        lines.append(f"title: {name}")
        lines.append(f"type: contact")
        if c.get('email'):
            lines.append(f"email: {c['email']}")
        if c.get('phone'):
            lines.append(f"phone: {c['phone']}")
        if c.get('company'):
            lines.append(f"company: {c['company']}")
        if c.get('role'):
            lines.append(f"role: {c['role']}")
        if c.get('source'):
            lines.append(f"source: {c['source']}")
        lines.append(f"created_at: {c.get('created_at', '')}")
        lines.append("---")
        lines.append("")

        if c.get('context_notes'):
            notes_with_links = _add_backlinks(c['context_notes'], contact_names)
            lines.append(notes_with_links)
            lines.append("")

        if c.get('last_interaction'):
            lines.append(f"Last interaction: {c['last_interaction']}")
            lines.append("")

        with open(filepath, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))

        stats["contacts"] += 1

    # --- Entitas ---
    cur.execute("SELECT * FROM entities ORDER BY name")
    entities = [dict(r) for r in cur.fetchall()]
    entity_names = [e['name'] for e in entities if e.get('name')]
    all_link_names = contact_names + entity_names

    for e in entities:
        name = e.get('name', '(no name)')
        filename = _sanitize_filename(name) + ".md"
        filepath = entities_dir / filename

        lines = []
        lines.append("---")
        lines.append(f"title: {name}")
        lines.append(f"type: entity")
        lines.append(f"entity_type: {e.get('entity_type', 'unknown')}")
        if e.get('source'):
            lines.append(f"source: {e['source']}")
        lines.append(f"created_at: {e.get('created_at', '')}")
        lines.append("---")
        lines.append("")

        if e.get('context_notes'):
            notes_with_links = _add_backlinks(e['context_notes'], all_link_names)
            lines.append(notes_with_links)
            lines.append("")

        with open(filepath, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))

        stats["entities"] += 1

    # --- Entri knowledge ---
    knowledge = [dict(r) for r in search_knowledge(conn, "", limit=10000)]

    for k in knowledge:
        title = k.get('title', '(no title)')
        filename = _sanitize_filename(title) + ".md"
        filepath = knowledge_dir / filename

        lines = []
        lines.append("---")
        lines.append(f"title: {title}")
        lines.append(f"type: knowledge")
        lines.append(f"entry_type: {k.get('entry_type', 'unknown')}")
        if k.get('source'):
            lines.append(f"source: {k['source']}")
        lines.append(f"created_at: {k.get('created_at', '')}")
        lines.append("---")
        lines.append("")

        content = k.get('content', '')
        content_with_links = _add_backlinks(content, all_link_names)
        lines.append(content_with_links)
        lines.append("")

        if k.get('related_contact_ids'):
            try:
                related_ids = json.loads(k['related_contact_ids'])
                if related_ids:
                    lines.append("## Related Contacts")
                    lines.append("")
                    for rid in related_ids:
                        cur.execute("SELECT name FROM contacts WHERE id = ?", (rid,))
                        row = cur.fetchone()
                        if row:
                            lines.append(f"- [[{row['name']}]]")
                    lines.append("")
            except (json.JSONDecodeError, TypeError):
                pass

        with open(filepath, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))

        stats["knowledge"] += 1

    # --- Notes ---
    notes = [dict(r) for r in search_notes(conn, "", limit=10000)]

    for n in notes:
        title = n.get('title') or n.get('filename', '(no title)')
        filename = _sanitize_filename(title) + ".md"
        filepath = notes_dir / filename

        lines = []
        lines.append("---")
        lines.append(f"title: {title}")
        lines.append(f"type: note")
        if n.get('filename'):
            lines.append(f"file: {n['filename']}")
        if n.get('tags'):
            lines.append(f"tags: {n['tags']}")
        lines.append(f"created_at: {n.get('created_at', '')}")
        lines.append("---")
        lines.append("")

        content = n.get('content', '')
        content_with_links = _add_backlinks(content, all_link_names)
        lines.append(content_with_links)
        lines.append("")

        if n.get('summary'):
            lines.append(f"## Summary")
            lines.append("")
            lines.append(n['summary'])
            lines.append("")

        with open(filepath, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))

        stats["notes"] += 1

    # --- Relasi knowledge graph ---
    cur.execute("""
        SELECT er.*, s.name AS source_name, t.name AS target_name
        FROM entity_relations er
        JOIN entities s ON er.source_entity_id = s.id
        JOIN entities t ON er.target_entity_id = t.id
    """)
    relations = [dict(r) for r in cur.fetchall()]
    stats["relations"] = len(relations)

    graph_lines = ["# Knowledge Graph", ""]
    graph_lines.append(f"Exported at: {datetime.utcnow().isoformat()}")
    graph_lines.append("")
    graph_lines.append("## Entities")
    graph_lines.append("")
    for e in entities:
        graph_lines.append(f"- [[{e['name']}]] ({e.get('entity_type', 'unknown')})")
    graph_lines.append("")
    graph_lines.append("## Relations")
    graph_lines.append("")
    for r in relations:
        graph_lines.append(
            f"- [[{r['source_name']}]] →{r.get('relation_type', 'related')}→ "
            f"[[{r['target_name']}]]"
        )
    graph_lines.append("")

    with open(output / "graph.md", "w", encoding="utf-8") as f:
        f.write("\n".join(graph_lines))

    index_lines = ["# Personal Brain Index", ""]
    index_lines.append(f"Exported at: {datetime.utcnow().isoformat()}")
    index_lines.append("")
    index_lines.append("## Contacts")
    index_lines.append("")
    for c in contacts:
        index_lines.append(f"- [[{c['name']}]]")
    index_lines.append("")
    index_lines.append("## Entities")
    index_lines.append("")
    for e in entities:
        index_lines.append(f"- [[{e['name']}]] ({e.get('entity_type', 'unknown')})")
    index_lines.append("")
    index_lines.append("## Knowledge Entries")
    index_lines.append("")
    for k in knowledge:
        title = k.get('title', '(no title)')
        index_lines.append(f"- [[{title}]]")
    index_lines.append("")
    index_lines.append("## Notes")
    index_lines.append("")
    for n in notes:
        index_lines.append(f"- [[{n.get('title') or n.get('filename', '(no title)')}]]")
    index_lines.append("")

    with open(output / "index.md", "w", encoding="utf-8") as f:
        f.write("\n".join(index_lines))

    return stats


def import_brain_markdown_with_backlinks(conn, input_folder):
    """Import brain data dari folder markdown dengan backlinks.

    Membaca file .md dari subfolder contacts/, knowledge/, notes/, entities/
    dan mengimpornya ke database. Backlinks [[Nama]] dipertahankan sebagai teks.

    Args:
        conn: Database connection
        input_folder: Folder yang berisi file markdown export

    Returns:
        dict dengan statistik import per kategori
    """
    from pathlib import Path
    import re

    input_path = Path(input_folder)
    if not input_path.exists():
        raise FileNotFoundError(f"Input folder not found: {input_folder}")

    stats = {
        "contacts": 0,
        "knowledge": 0,
        "notes": 0,
        "entities": 0,
    }

    cur = conn.cursor()

    entities_dir = input_path / "entities"
    if entities_dir.exists():
        for md_file in entities_dir.glob("*.md"):
            content = md_file.read_text(encoding="utf-8")
            frontmatter, body = _parse_frontmatter(content)
            name = frontmatter.get("title", md_file.stem)
            entity_type = frontmatter.get("entity_type", "concept")
            source = frontmatter.get("source", "import")
            context_notes = body.strip() if body.strip() else None

            upsert_entity(
                conn, name=name, entity_type=entity_type,
                source=source, context_notes=context_notes,
            )
            stats["entities"] += 1

    contacts_dir = input_path / "contacts"
    if contacts_dir.exists():
        for md_file in contacts_dir.glob("*.md"):
            content = md_file.read_text(encoding="utf-8")
            frontmatter, body = _parse_frontmatter(content)
            name = frontmatter.get("title", md_file.stem)
            email = frontmatter.get("email")
            phone = frontmatter.get("phone")
            company = frontmatter.get("company")
            role = frontmatter.get("role")
            source = frontmatter.get("source", "import")
            context_notes = body.strip() if body.strip() else None

            upsert_contact(
                conn, name=name, email=email, phone=phone,
                company=company, role=role, source=source,
                context_notes=context_notes,
            )
            stats["contacts"] += 1

    knowledge_dir = input_path / "knowledge"
    if knowledge_dir.exists():
        for md_file in knowledge_dir.glob("*.md"):
            content = md_file.read_text(encoding="utf-8")
            frontmatter, body = _parse_frontmatter(content)
            title = frontmatter.get("title", md_file.stem)
            entry_type = frontmatter.get("entry_type", "finding")
            source = frontmatter.get("source", "import")
            content_text = body.strip() if body.strip() else ""

            insert_knowledge_entry(
                conn, entry_type=entry_type, title=title,
                content=content_text, source=source,
            )
            stats["knowledge"] += 1

    notes_dir = input_path / "notes"
    if notes_dir.exists():
        for md_file in notes_dir.glob("*.md"):
            content = md_file.read_text(encoding="utf-8")
            frontmatter, body = _parse_frontmatter(content)
            title = frontmatter.get("title", md_file.stem)
            tags_raw = frontmatter.get("tags", "")
            tags = [t.strip() for t in tags_raw.split(",") if t.strip()] if tags_raw else []
            content_text = body.strip() if body.strip() else ""

            upsert_note(
                conn, filename=md_file.name, file_path=str(md_file),
                title=title, content=content_text, tags=tags,
            )
            stats["notes"] += 1

    return stats


def _parse_frontmatter(content):
    """Parse YAML frontmatter dari markdown content.

    Returns (frontmatter_dict, body_text).
    """
    import re

    match = re.match(r'^---\s*\n(.*?)\n---\s*\n(.*)', content, re.DOTALL)
    if not match:
        return {}, content

    fm_text = match.group(1)
    body = match.group(2)

    frontmatter = {}
    for line in fm_text.split('\n'):
        if ':' in line:
            key, _, value = line.partition(':')
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            frontmatter[key] = value

    return frontmatter, body
