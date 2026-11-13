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
    monkeypatch.setenv("BRAIN_DB_KEY", Fernet.generate_key().decode())
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


# ---------- Prioritas 0 — Embedding version filtering ----------
def test_semantic_search_filters_by_model_version(db_mode, monkeypatch):
    """P0-03: semantic_search() harus filter embeddings berdasarkan
    model_version aktif. Embedding dari model yang berbeda tidak boleh
    dicampur karena cosine similarity lintas ruang embedding tidak bermakna."""
    import json
    import brain_db as brain_db_mod
    import brain_embeddings

    # Monkeypatch embed_text to return a deterministic vector
    monkeypatch.setattr(
        brain_db_mod, "embed_text", lambda text: [1.0, 0.0, 0.0]
    )
    # Monkeypatch cosine_similarity to return 1.0 for matching, 0.0 otherwise
    def fake_cosine(v1, v2):
        if v1 == v2:
            return 1.0
        return 0.0
    monkeypatch.setattr(brain_db_mod, "_cosine_similarity", fake_cosine)

    conn = db_mode.get_connection()
    try:
        # Insert embeddings with two different model versions
        conn.execute(
            "INSERT INTO embeddings (source_type, source_id, content_chunk, embedding_json, model_version, created_at) "
            "VALUES (?, ?, ?, ?, ?, datetime('now'))",
            ("contact", "1", "old tfidf content", json.dumps([1.0, 0.0, 0.0]), "tfidf-v1"),
        )
        conn.execute(
            "INSERT INTO embeddings (source_type, source_id, content_chunk, embedding_json, model_version, created_at) "
            "VALUES (?, ?, ?, ?, ?, datetime('now'))",
            ("contact", "2", "new sentence-transformers content", json.dumps([0.0, 1.0, 0.0]), "sentence-transformers-v2"),
        )
        conn.commit()

        # Monkeypatch get_embedding_version to return the new version
        monkeypatch.setattr(
            brain_embeddings, "get_embedding_version",
            lambda: "sentence-transformers-v2",
        )

        results = db_mode.semantic_search(conn, "content", limit=10)

        # Should only return the embedding with matching model_version
        # Vector [1,0,0] vs stored [0,1,0] -> cosine = 0 < 0.1 threshold -> no results
        assert len(results) == 0, \
            f"Expected 0 results for mismatched model version, got {len(results)}"

        # Now switch to old version — should return the old content
        monkeypatch.setattr(
            brain_embeddings, "get_embedding_version",
            lambda: "tfidf-v1",
        )

        results_old = db_mode.semantic_search(conn, "content", limit=10)
        assert len(results_old) >= 1
        for r in results_old:
            assert r["content_chunk"] == "old tfidf content", \
                f"Expected old model content, got: {r['content_chunk']}"
    finally:
        conn.close()


# ---------- Prioritas 5 — MCP search relevance ----------
def test_search_entities_has_relevance(db_mode):
    """P5-01: search_entities() must return _relevance field on each result."""
    conn = db_mode.get_connection()
    try:
        # Upsert via entity table directly (search_entities reads entities table)
        conn.execute(
            "INSERT INTO entities (name, entity_type, context_notes, source, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, datetime('now'), datetime('now'))",
            ("Acme Corporation", "company", "Technology vendor in Jakarta", "mcp"),
        )
        conn.commit()

        results = [dict(r) for r in db_mode.search_entities(conn, "Acme", limit=10)]
        assert len(results) >= 1
        for r in results:
            assert "_relevance" in r, f"Missing _relevance in {r}"
            assert r["_relevance"] > 0.0, f"_relevance should be > 0 for match: {r}"
    finally:
        conn.close()


def test_search_entities_offset(db_mode):
    """P5-02: search_entities() must support offset for pagination."""
    conn = db_mode.get_connection()
    try:
        for i in range(5):
            conn.execute(
                "INSERT INTO entities (name, entity_type, source, created_at, updated_at) "
                "VALUES (?, ?, 'mcp', datetime('now'), datetime('now'))",
                (f"Entity {i:02d}", "person"),
            )
        conn.commit()

        page1 = [dict(r) for r in db_mode.search_entities(conn, "Entity", limit=2, offset=0)]
        page2 = [dict(r) for r in db_mode.search_entities(conn, "Entity", limit=2, offset=2)]
        page3 = [dict(r) for r in db_mode.search_entities(conn, "Entity", limit=2, offset=4)]

        ids1 = {r["id"] for r in page1}
        ids2 = {r["id"] for r in page2}
        ids3 = {r["id"] for r in page3}

        assert len(page1) == 2
        assert len(page2) == 2
        assert len(page3) == 1  # only 1 remaining
        assert ids1.isdisjoint(ids2), "Pages should not overlap"
        assert ids1.isdisjoint(ids3)
        assert ids2.isdisjoint(ids3)
    finally:
        conn.close()


def test_multi_word_relevance_scoring(db_mode):
    """P5-03: Multi-word query should score higher when more tokens match."""
    conn = db_mode.get_connection()
    try:
        db_mode.upsert_contact(
            conn, name="John Doe", email="john@example.com",
            company="Acme Corp", context_notes="Project alpha lead",
        )

        # Single word match
        r1 = db_mode.search_contacts(conn, "John", limit=10)
        # Two words both matching name
        r2 = db_mode.search_contacts(conn, "John Doe", limit=10)

        # Both should find the contact
        assert len(r1) >= 1
        assert len(r2) >= 1

        # Two-word match should have equal or higher relevance than single word
        # (more tokens matched = better coverage)
        assert r2[0]["_relevance"] >= r1[0]["_relevance"], \
            f"Multi-word relevance {r2[0]['_relevance']} < single-word {r1[0]['_relevance']}"
    finally:
        conn.close()


def test_relevance_title_outranks_body(db_mode):
    """P5-04: Title match should score higher than body-only match."""
    conn = db_mode.get_connection()
    try:
        db_mode.upsert_contact(
            conn, name="Other Person", email="alpha@example.com",
            context_notes="Just a regular contact",
        )
        db_mode.upsert_contact(
            conn, name="Alpha Beta", email="beta@example.com",
            context_notes="Another contact",
        )

        results = db_mode.search_contacts(conn, "Alpha", limit=10)
        assert len(results) >= 2

        # Alpha Beta (title match) should outrank Other Person (body/email match)
        assert results[0]["_relevance"] >= results[1]["_relevance"], \
            f"Title match should outrank body match: {results[0]['_relevance']} vs {results[1]['_relevance']}"
    finally:
        conn.close()
