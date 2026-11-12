"""Unit tests for brain_db core operations (contacts, notes, knowledge,
entities, relations, search, provenance, access control).

Runs in both plain SQLite and SQLCipher-encrypted mode via the db_mode
fixture (inherited from test_db_concurrency.py pattern).
"""

import pytest
from cryptography.fernet import Fernet


@pytest.fixture(params=["plain", "encrypted"])
def db_mode(request, monkeypatch, tmp_path):
    if request.param == "encrypted":
        monkeypatch.setenv("BRAIN_DB_KEY", Fernet.generate_key().decode())
    else:
        monkeypatch.delenv("BRAIN_DB_KEY", raising=False)
    import brain_db
    monkeypatch.setattr(brain_db, "DB_PATH", tmp_path / "brain_test.db")
    monkeypatch.setattr(brain_db, "ENCRYPTED_PATH", tmp_path / "brain_test.db.enc")
    return brain_db


# ---------- Contacts ----------

def test_upsert_and_search_contact(db_mode):
    conn = db_mode.get_connection()
    try:
        cid = db_mode.upsert_contact(
            conn, name="Alice", email="alice@example.com",
            phone="0812345678", company="Acme", role="Engineer",
            source="web", context_notes="Likes coffee",
        )
        assert cid is not None

        results = [dict(r) for r in db_mode.search_contacts(conn, "Alice", limit=10)]
        assert len(results) == 1
        assert results[0]["name"] == "Alice"
        assert results[0]["email"] == "alice@example.com"
    finally:
        conn.close()


def test_update_contact(db_mode):
    conn = db_mode.get_connection()
    try:
        cid = db_mode.upsert_contact(conn, name="Bob", email="bob@example.com")
        # Update non-identity fields (email/name are lookup keys in upsert_contact)
        db_mode.upsert_contact(
            conn, name="Bob", email="bob@example.com",
            company="NewCo", role="Manager",
        )
        results = [dict(r) for r in db_mode.search_contacts(conn, "Bob", limit=10)]
        assert len(results) == 1
        assert results[0]["email"] == "bob@example.com"
        assert results[0]["company"] == "NewCo"
        assert results[0]["role"] == "Manager"
    finally:
        conn.close()


def test_delete_contact(db_mode):
    conn = db_mode.get_connection()
    try:
        cid = db_mode.upsert_contact(conn, name="ToDelete")
        assert db_mode.delete_contact(conn, cid) is True
        assert db_mode.delete_contact(conn, cid) is False  # already gone
        results = [dict(r) for r in db_mode.search_contacts(conn, "ToDelete", limit=10)]
        assert results == []
    finally:
        conn.close()


# ---------- Notes ----------

def test_upsert_and_search_note(db_mode):
    conn = db_mode.get_connection()
    try:
        nid = db_mode.upsert_note(
            conn, filename="note1.md", file_path="/notes/note1.md",
            title="Meeting", content="Discussed project X",
            summary="Short summary", tags=["work", "project"],
        )
        assert nid is not None

        results = [dict(r) for r in db_mode.search_notes(conn, "Meeting", limit=10)]
        assert len(results) == 1
        assert results[0]["title"] == "Meeting"
    finally:
        conn.close()


def test_delete_note(db_mode):
    conn = db_mode.get_connection()
    try:
        nid = db_mode.upsert_note(
            conn, filename="t.md", file_path="/t.md",
            title="Temp", content="temp content",
        )
        assert db_mode.delete_note(conn, nid) is True
        assert db_mode.delete_note(conn, nid) is False
    finally:
        conn.close()


# ---------- Knowledge entries ----------

def test_knowledge_entry_crud(db_mode):
    conn = db_mode.get_connection()
    try:
        eid = db_mode.insert_knowledge_entry(
            conn, entry_type="finding", title="Finding A",
            content="Important finding", source="research",
        )
        assert eid is not None

        results = [dict(r) for r in db_mode.search_knowledge(conn, "Finding", limit=10)]
        assert len(results) == 1
        assert results[0]["title"] == "Finding A"

        assert db_mode.delete_knowledge_entry(conn, eid) is True
        assert db_mode.delete_knowledge_entry(conn, eid) is False
    finally:
        conn.close()


# ---------- Entities and relations (knowledge graph) ----------

def test_entity_and_relation(db_mode):
    conn = db_mode.get_connection()
    try:
        e1 = db_mode.upsert_entity(conn, name="Acme Corp", entity_type="company")
        e2 = db_mode.upsert_entity(conn, name="Alice", entity_type="person")
        assert e1 != e2

        rid = db_mode.add_entity_relation(
            conn, source_entity_id=e1, target_entity_id=e2,
            relation_type="works_at", source="manual", confidence=0.9,
        )
        assert rid is not None

        entities = [dict(r) for r in db_mode.search_entities(conn, "Acme", limit=10)]
        assert len(entities) == 1
        assert entities[0]["entity_type"] == "company"

        rels = db_mode.get_entity_relations(conn, e1, limit=10)
        assert len(rels) == 1
        assert rels[0]["relation_type"] == "works_at"

        assert db_mode.delete_entity_relation(conn, rid) is True
        assert db_mode.delete_entity(conn, e1) is True
        assert db_mode.delete_entity(conn, e2) is True
    finally:
        conn.close()


# ---------- Knowledge graph query ----------

def test_query_knowledge_graph(db_mode):
    conn = db_mode.get_connection()
    try:
        e1 = db_mode.upsert_entity(conn, name="Corp X", entity_type="company")
        e2 = db_mode.upsert_entity(conn, name="Person Y", entity_type="person")
        db_mode.add_entity_relation(
            conn, e1, e2, "works_at", source="manual", confidence=1.0,
        )

        graph = db_mode.query_knowledge_graph(conn, "Corp", limit=10)
        assert "entities" in graph
        assert "relations" in graph
        assert len(graph["entities"]) >= 1
    finally:
        conn.close()


# ---------- Provenance ----------

def test_add_provenance(db_mode):
    conn = db_mode.get_connection()
    try:
        cid = db_mode.upsert_contact(conn, name="Prov Test")
        pid = db_mode.add_provenance(
            conn, subject_type="contact", subject_id=cid,
            source_type="manual", source_detail="added by test",
            confidence=1.0,
        )
        assert pid is not None
    finally:
        conn.close()


# ---------- Access control (users / item_shares) ----------

def test_share_and_accessible_items(db_mode):
    conn = db_mode.get_connection()
    try:
        # Create two users via raw SQL (users table exists from _create_access_control_tables)
        conn.execute("INSERT INTO users (username, password_hash) VALUES (?, ?)",
                     ("alice", "hash1"))
        conn.execute("INSERT INTO users (username, password_hash) VALUES (?, ?)",
                     ("bob", "hash2"))
        conn.commit()

        cid = db_mode.upsert_contact(conn, name="Shared Contact")
        # Set owner via raw SQL (upsert_contact has no owner_user_id param)
        conn.execute("UPDATE contacts SET owner_user_id = 1 WHERE id = ?", (cid,))
        conn.commit()

        db_mode.share_item(
            conn, item_type="contact", item_id=cid,
            owner_user_id=1, shared_with_user_id=2, access_level="read",
        )

        items = db_mode.get_accessible_items(conn, user_id=2, item_type="contact", limit=10)
        assert len(items) >= 1
        assert any(dict(r).get("id") == cid for r in items)
    finally:
        conn.close()


# ---------- Global and semantic search ----------

def test_global_and_semantic_search(db_mode):
    conn = db_mode.get_connection()
    try:
        db_mode.upsert_contact(conn, name="Search Me", email="search@example.com")
        db_mode.upsert_note(
            conn, filename="s.md", file_path="/s.md",
            title="Search Note", content="unique keyword zebra",
        )

        results = db_mode.global_search(conn, "Search", limit=10)
        assert isinstance(results, dict)

        semantic = db_mode.semantic_search(conn, "zebra", limit=10)
        # semantic may return empty with deterministic TF-IDF fallback — just
        # ensure it does not crash
        assert isinstance(semantic, list)
    finally:
        conn.close()
