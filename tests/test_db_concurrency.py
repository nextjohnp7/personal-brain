import pytest
from cryptography.fernet import Fernet


@pytest.fixture(params=[
    "plain",
    "encrypted",
])
def db_mode(request, monkeypatch, tmp_path):
    if request.param == "encrypted":
        monkeypatch.setenv("BRAIN_DB_KEY", Fernet.generate_key().decode())
    monkeypatch.setenv("BRAIN_DB_KEY", Fernet.generate_key().decode())
    import brain_db
    monkeypatch.setattr(brain_db, "DB_PATH", tmp_path / "brain_test.db")
    monkeypatch.setattr(brain_db, "ENCRYPTED_PATH", tmp_path / "brain_test.db.enc")
    return brain_db

def test_two_connections_do_not_lose_writes(db_mode):
    brain_db = db_mode
    c1 = brain_db.get_connection()   # e.g. dashboard
    c2 = brain_db.get_connection()   # e.g. sync daemon
    brain_db.upsert_contact(c1, name="Dari Dashboard")
    brain_db.upsert_contact(c2, name="Dari Daemon")
    c2.close()
    c1.close()
    c3 = brain_db.get_connection()
    names = {r["name"] for r in brain_db.search_contacts(c3, "", limit=100)}
    c3.close()
    assert {"Dari Dashboard", "Dari Daemon"} <= names
