import re

import pytest
from cryptography.fernet import Fernet


def _login(c):
    """Login helper: post test credentials."""
    c.post("/login", data={"username": "tester", "password": "rahasia-test-123"})


def test_csrf_rejects_post_without_token(dashboard, monkeypatch):
    monkeypatch.setitem(dashboard.app.config, "WTF_CSRF_ENABLED", True)
    c = dashboard.app.test_client()
    assert c.post("/contacts/add", data={"name": "X"}).status_code == 400


def test_csrf_accepts_valid_token(logged_in_client, monkeypatch):
    monkeypatch.setitem(logged_in_client.application.config, "WTF_CSRF_ENABLED", True)
    c = logged_in_client
    page = c.get("/contacts/add").get_data(as_text=True)
    token = re.search(r'name="csrf_token" value="([^"]+)"', page).group(1)
    r = c.post("/contacts/add", data={"name": "X", "csrf_token": token})
    assert r.status_code == 302


def test_open_redirect_blocked(auth_dashboard):
    c = auth_dashboard.app.test_client()
    r = c.post("/login?next=https://evil.example.com",
               data={"username": "tester", "password": "rahasia-test-123"})
    assert r.status_code == 302
    assert "evil.example.com" not in r.headers["Location"]


def test_login_rate_limited(dashboard, monkeypatch):
    monkeypatch.setitem(dashboard.app.config, "RATELIMIT_ENABLED", True)
    c = dashboard.app.test_client()
    codes = [c.post("/login", data={"username": "x", "password": "y"}).status_code
             for _ in range(7)]
    assert 429 in codes


def test_security_headers_present(dashboard):
    """Every response must carry protective security headers (F-06)."""
    c = dashboard.app.test_client()
    resp = c.get("/login")
    assert resp.status_code == 200
    assert resp.headers.get("X-Content-Type-Options") == "nosniff"
    assert resp.headers.get("X-Frame-Options") == "DENY"
    assert resp.headers.get("Referrer-Policy") == "strict-origin-when-cross-origin"


# ---------- F-01: Fail-closed enkripsi database ----------
def test_brain_db_key_required(monkeypatch):
    """F-01: get_connection() gagal jika BRAIN_DB_KEY tidak di-set."""
    monkeypatch.delenv("BRAIN_DB_KEY", raising=False)
    import importlib
    import src.brain_db as brain_db
    importlib.reload(brain_db)
    try:
        brain_db.get_connection()
        assert False, "Harus raise ValueError saat BRAIN_DB_KEY tidak di-set"
    except ValueError as e:
        assert "BRAIN_DB_KEY" in str(e)


# ---------- F-05: SQL table name allowlist ----------
def test_validate_table_name_rejects_malicious():
    """F-05: _validate_table_name() tolak nama tabel berbahaya (SQL injection)."""
    import src.brain_db as brain_db
    bad_names = [
        "contacts; DROP TABLE users--",
        "contacts' OR 1=1--",
        "contacts\" OR 1=1--",
        "contacts DROP TABLE users",
        "../../etc/passwd",
        "select * from users",
        "",
        "123contacts",
    ]
    for name in bad_names:
        try:
            brain_db._validate_table_name(name)
            assert False, f"Harus tolak nama tabel: {name!r}"
        except ValueError:
            pass

    # Nama valid harus lolos
    for name in ["contacts", "notes", "knowledge_entries", "_internal"]:
        assert brain_db._validate_table_name(name) == name


# ---------- Prioritas 4 — Performance & Optimasi ----------
EXPECTED_INDEXES = {
    "idx_contacts_email",
    "idx_contacts_name",
    "idx_emails_sender",
    "idx_emails_recipient",
    "idx_emails_received_at",
    "idx_notes_title",
    "idx_notes_updated_at",
    "idx_knowledge_title",
    "idx_knowledge_created_at",
    "idx_entities_name",
    "idx_entities_type",
    "idx_entity_relations_source",
    "idx_entity_relations_target",
    "idx_entity_relations_type",
    "idx_item_shares_item",
    "idx_item_shares_user",
    "idx_embeddings_source_type",
    "idx_conversations_created_at",
    "idx_provenance_subject",
}


@pytest.fixture()
def db_mode(monkeypatch, tmp_path):
    """Encrypted temp DB fixture for performance tests (mirrors other test files)."""
    monkeypatch.setenv("BRAIN_DB_KEY", Fernet.generate_key().decode())
    import brain_db
    monkeypatch.setattr(brain_db, "DB_PATH", tmp_path / "brain_test.db")
    monkeypatch.setattr(brain_db, "ENCRYPTED_PATH", tmp_path / "brain_test.db.enc")
    return brain_db


def test_indexes_exist(db_mode):
    """A-01: Semua indeks performa harus ada di sqlite_master setelah inisialisasi."""
    conn = db_mode.get_connection()
    try:
        cur = conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'index' "
            "AND name LIKE 'idx_%'"
        )
        actual = {row[0] for row in cur.fetchall()}
    finally:
        conn.close()

    missing = EXPECTED_INDEXES - actual
    assert not missing, f"Indeks hilang: {missing}"


def test_search_pagination_limits_results(db_mode):
    """A-02: Fungsi search harus menghormati LIMIT/OFFSET (tidak mengembalikan semua baris)."""
    conn = db_mode.get_connection()
    try:
        # Sisipkan 25 kontak
        for i in range(25):
            db_mode.upsert_contact(
                conn, name=f"User {i:02d}", email=f"user{i:02d}@example.com"
            )

        # Halaman 1: maksimal 20
        page1 = db_mode.search_contacts(conn, "User", limit=20, offset=0)
        assert len(page1) <= 20

        # Halaman 2: sisanya (5 kontak)
        page2 = db_mode.search_contacts(conn, "User", limit=20, offset=20)
        assert len(page2) <= 5

        # Total harus 25
        total = len(page1) + len(page2)
        assert total == 25, f"Total {total}, ekspektasi 25"
    finally:
        conn.close()


def test_embed_text_cache_returns_same_vector():
    """A-03: embed_text() harus mengembalikan vektor yang sama untuk teks yang sama (cache hit)."""
    import src.brain_embeddings as be

    text = "halo ini adalah teks uji untuk cache embeddings"
    v1 = be.embed_text(text)
    v2 = be.embed_text(text)

    assert v1 == v2, "Cache harus mengembalikan vektor identik untuk teks yang sama"
    # Cache internal harus menyimpan entri
    assert text in be._embed_cache
    assert len(be._embed_cache) <= be._EMBED_CACHE_MAX



