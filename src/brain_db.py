"""Personal Brain Database - SQLite database for agent knowledge base.

All data is stored in a single SQLite file on the user's computer.
No server is required.
"""

import sqlite3
import json
import os
from pathlib import Path
from datetime import datetime

# Database location at project root
PROJECT_ROOT = Path(__file__).parent.parent.resolve()
DB_PATH = PROJECT_ROOT / "brain.db"


def get_connection():
    """Open a connection to the SQLite database. Create tables if they do not exist."""
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    _create_tables(conn)
    return conn


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
            context_notes TEXT,    -- free-form notes about this person
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
            contact_id INTEGER,    -- FK to contacts (organizer or primary attendee)
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

    # Agent conversation history (for learning over time)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS conversations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_message TEXT,
            agent_response TEXT,
            context_used TEXT,     -- JSON array of source IDs used
            created_at TEXT DEFAULT (datetime('now'))
        )
    """)

    # Free-form knowledge entries (findings, connections between people, ideas)
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

    # Embeddings for semantic search (content chunks with TF-IDF vectors)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS embeddings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_type TEXT,       -- 'contact', 'email', 'note', 'event', 'knowledge'
            source_id INTEGER,      -- FK to the source table
            content_chunk TEXT,     -- the text that was embedded
            embedding_json TEXT,    -- JSON array of TF-IDF floats
            created_at TEXT DEFAULT (datetime('now'))
        )
    """)

    # Entities for knowledge graph (deduplicated people, companies, concepts)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS entities (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            entity_type TEXT,       -- 'person', 'company', 'concept', 'project'
            source TEXT,            -- 'manual', 'conversation', 'gmail', 'calendar'
            context_notes TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
        )
    """)

    # Entity relations (knowledge graph edges)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS entity_relations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_entity_id INTEGER,   -- FK to entities.id
            target_entity_id INTEGER,   -- FK to entities.id
            relation_type TEXT,         -- 'works_at', 'knows', 'met_with', 'mentioned_in'
            source TEXT,                -- 'conversation', 'email', 'manual'
            confidence REAL DEFAULT 1.0,
            created_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (source_entity_id) REFERENCES entities(id),
            FOREIGN KEY (target_entity_id) REFERENCES entities(id)
        )
    """)

    conn.commit()


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
# Embeddings for Semantic Search (TF-IDF, no external dependencies)
# =====================================================================

def _tokenize(text):
    """Simple tokenizer: lowercase, split on non-alphanumeric."""
    import re
    return [t for t in re.split(r'[^a-z0-9]+', text.lower()) if t]


def _compute_tfidf(tokens, all_token_lists):
    """Compute TF-IDF vector for a token list given a corpus of token lists."""
    import math
    from collections import Counter

    # Term frequency for this document
    tf = Counter(tokens)
    total = len(tokens)
    if total == 0:
        return {}

    # Document frequency across corpus
    df = Counter()
    for doc_tokens in all_token_lists:
        df.update(set(doc_tokens))

    n_docs = len(all_token_lists)
    vector = {}
    for term, count in tf.items():
        tf_val = count / total
        idf_val = math.log(n_docs / (1 + df[term])) + 1.0
        vector[term] = tf_val * idf_val
    return vector


def _cosine_similarity(vec_a, vec_b):
    """Cosine similarity between two sparse vectors (dicts)."""
    import math
    if not vec_a or not vec_b:
        return 0.0
    dot = sum(vec_a.get(k, 0) * vec_b.get(k, 0) for k in set(vec_a) | set(vec_b))
    norm_a = math.sqrt(sum(v * v for v in vec_a.values()))
    norm_b = math.sqrt(sum(v * v for v in vec_b.values()))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def store_embedding(conn, source_type, source_id, content_chunk):
    """Compute and store a TF-IDF embedding for a content chunk."""
    tokens = _tokenize(content_chunk)

    # Get all existing chunks to compute IDF
    cur = conn.cursor()
    cur.execute("SELECT content_chunk FROM embeddings")
    existing_chunks = [row[0] for row in cur.fetchall()]
    all_token_lists = [_tokenize(c) for c in existing_chunks] + [tokens]

    vector = _compute_tfidf(tokens, all_token_lists)

    # Recompute all embeddings since IDF changed (simple approach for small corpus)
    cur.execute("SELECT id, content_chunk FROM embeddings")
    for row in cur.fetchall():
        chunk_tokens = _tokenize(row[1])
        chunk_vector = _compute_tfidf(chunk_tokens, all_token_lists)
        cur.execute(
            "UPDATE embeddings SET embedding_json = ? WHERE id = ?",
            (json.dumps(chunk_vector), row[0])
        )

    cur.execute(
        "INSERT INTO embeddings (source_type, source_id, content_chunk, embedding_json) "
        "VALUES (?, ?, ?, ?)",
        (source_type, source_id, content_chunk, json.dumps(vector))
    )
    conn.commit()
    return cur.lastrowid


def semantic_search(conn, query, limit=10):
    """Semantic search using TF-IDF + cosine similarity.

    Returns list of dicts with source_type, source_id, content_chunk, similarity.
    """
    import math
    from collections import Counter

    query_tokens = _tokenize(query)

    # Get all embeddings with their vectors
    cur = conn.cursor()
    cur.execute("SELECT id, source_type, source_id, content_chunk, embedding_json FROM embeddings")
    rows = cur.fetchall()

    if not rows:
        return []

    # Recompute IDF with query included
    all_chunks = [r[3] for r in rows] + [query]
    all_token_lists = [_tokenize(c) for c in all_chunks]

    query_vector = _compute_tfidf(query_tokens, all_token_lists)

    results = []
    for row in rows:
        vec = json.loads(row[4]) if row[4] else {}
        sim = _cosine_similarity(query_vector, vec)
        if sim > 0:
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
    conn.commit()
    return cur.lastrowid


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
