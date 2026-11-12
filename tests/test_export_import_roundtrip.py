"""Roundtrip tests for JSON and Markdown export/import.

Exports brain data, then imports it back and verifies data integrity.
"""

import json
import os
import tempfile
from pathlib import Path

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


def _seed_brain(db_mode):
    """Insert a known set of records; return identifiers for verification."""
    conn = db_mode.get_connection()
    try:
        cid = db_mode.upsert_contact(
            conn, name="Roundtrip Contact", email="rt@example.com",
            phone="0800", company="RtCo", source="test",
            context_notes="roundtrip notes",
        )
        nid = db_mode.upsert_note(
            conn, filename="rt.md", file_path="/rt.md",
            title="Roundtrip Note", content="roundtrip content",
            summary="rt summary", tags=["rt"],
        )
        eid = db_mode.insert_knowledge_entry(
            conn, entry_type="finding", title="RT Finding",
            content="rt finding content", source="test",
        )
        ent1 = db_mode.upsert_entity(conn, name="RT Entity", entity_type="concept")
        db_mode.add_entity_relation(
            conn, ent1, cid, "related_to", source="test", confidence=0.5,
        )
        conn.commit()
        return {"contact_id": cid, "note_id": nid, "knowledge_id": eid, "entity_id": ent1}
    finally:
        conn.close()


def test_json_export_import_roundtrip(db_mode):
    """Export brain to JSON, import into a fresh DB, verify data survived."""
    import brain_db

    conn1 = db_mode.get_connection()
    try:
        ids = _seed_brain(db_mode)
        data = db_mode.export_brain_json(conn1)
    finally:
        conn1.close()

    # Import into a brand-new isolated DB
    tmp2 = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp2.close()
    os.unlink(tmp2.name)
    db2_path = Path(tmp2.name)

    monkeypatch_db = db_mode  # reuse module ref
    # Create a second module-level connection by reimporting with new path
    import importlib
    brain_db_copy = importlib.import_module("brain_db")
    brain_db_copy.DB_PATH = db2_path
    brain_db_copy.ENCRYPTED_PATH = Path(str(db2_path) + ".enc")

    conn2 = brain_db_copy.get_connection()
    try:
        result = brain_db_copy.import_brain_json(conn2, data)
        assert result["contacts"] >= 1
        assert result["notes"] >= 1
        assert result["knowledge_entries"] >= 1
        assert result["entities"] >= 1

        # Verify imported data
        contacts = [dict(r) for r in brain_db_copy.search_contacts(conn2, "Roundtrip", limit=10)]
        assert len(contacts) == 1
        assert contacts[0]["name"] == "Roundtrip Contact"
        assert contacts[0]["email"] == "rt@example.com"

        notes = [dict(r) for r in brain_db_copy.search_notes(conn2, "Roundtrip", limit=10)]
        assert len(notes) == 1
        assert notes[0]["title"] == "Roundtrip Note"

        knowledge = [dict(r) for r in brain_db_copy.search_knowledge(conn2, "RT", limit=10)]
        assert len(knowledge) == 1
        assert knowledge[0]["title"] == "RT Finding"

        entities = [dict(r) for r in brain_db_copy.search_entities(conn2, "RT Entity", limit=10)]
        assert len(entities) == 1
    finally:
        conn2.close()
        # cleanup
        for p in [db2_path, Path(str(db2_path) + ".enc")]:
            try:
                p.unlink()
            except FileNotFoundError:
                pass


def test_markdown_export_import_roundtrip(db_mode):
    """Export brain to markdown folder, import back, verify integrity."""
    import brain_db

    conn1 = db_mode.get_connection()
    try:
        _seed_brain(db_mode)
        export_dir = Path(tempfile.mkdtemp()) / "brain_export"
        stats = db_mode.export_brain_markdown_with_backlinks(conn1, str(export_dir))
    finally:
        conn1.close()

    assert stats["contacts"] >= 1
    assert stats["notes"] >= 1
    assert export_dir.exists()

    # Import into fresh DB
    tmp2 = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp2.close()
    os.unlink(tmp2.name)
    db2_path = Path(tmp2.name)

    import importlib
    brain_db_copy = importlib.import_module("brain_db")
    brain_db_copy.DB_PATH = db2_path
    brain_db_copy.ENCRYPTED_PATH = Path(str(db2_path) + ".enc")

    conn2 = brain_db_copy.get_connection()
    try:
        stats2 = db_mode.import_brain_markdown_with_backlinks(conn2, str(export_dir))
        assert stats2["contacts"] >= 1
        assert stats2["notes"] >= 1

        contacts = [dict(r) for r in brain_db_copy.search_contacts(conn2, "Roundtrip", limit=10)]
        assert len(contacts) == 1
        assert contacts[0]["name"] == "Roundtrip Contact"
    finally:
        conn2.close()
        # cleanup
        for p in [db2_path, Path(str(db2_path) + ".enc"), export_dir]:
            try:
                if p.is_dir():
                    import shutil
                    shutil.rmtree(p)
                else:
                    p.unlink()
            except FileNotFoundError:
                pass
