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


# ---------- Prioritas 1 — Gmail failed-message checkpoint ----------
def test_failed_gmail_message_roundtrip(db_mode):
    """P1-01: Gagal memproses pesan Gmail harus dicatat dan dapat diambil ulang."""
    conn = db_mode.get_connection()
    try:
        # Awalnya tidak ada pesan gagal
        assert db_mode.get_failed_gmail_messages(conn) == []

        # Catat pesan gagal
        db_mode.record_failed_gmail_message(
            conn, "msg-123", "temporary network error"
        )
        failed = db_mode.get_failed_gmail_messages(conn)
        assert len(failed) == 1
        assert failed[0]["message_id"] == "msg-123"
        assert failed[0]["error_message"] == "temporary network error"
        assert failed[0]["retry_count"] == 1
        assert failed[0]["failed_at"] is not None

        # Catat lagi pesan yang sama — retry_count harus bertambah
        db_mode.record_failed_gmail_message(conn, "msg-123", "timeout")
        failed = db_mode.get_failed_gmail_messages(conn)
        assert len(failed) == 1  # masih satu baris, di-update
        assert failed[0]["retry_count"] == 2
        assert failed[0]["error_message"] == "timeout"

        # Hapus setelah berhasil dicoba ulang
        db_mode.clear_failed_gmail_message(conn, "msg-123")
        assert db_mode.get_failed_gmail_messages(conn) == []
    finally:
        conn.close()


def test_failed_gmail_message_ordering(db_mode):
    """P1-01: Pesan gagal diurutkan berdasarkan failed_at (terlama dulu)."""
    conn = db_mode.get_connection()
    try:
        import time

        db_mode.record_failed_gmail_message(conn, "msg-b", "error b")
        time.sleep(0.01)
        db_mode.record_failed_gmail_message(conn, "msg-a", "error a")

        failed = db_mode.get_failed_gmail_messages(conn)
        assert len(failed) == 2
        assert failed[0]["message_id"] == "msg-b"  # lebih lama dulu
        assert failed[1]["message_id"] == "msg-a"
    finally:
        conn.close()


def test_clear_nonexistent_failed_gmail_message(db_mode):
    """P1-01: Menghapus pesan gagal yang tidak ada tidak menyebabkan error."""
    conn = db_mode.get_connection()
    try:
        db_mode.clear_failed_gmail_message(conn, "msg-does-not-exist")
        assert db_mode.get_failed_gmail_messages(conn) == []
    finally:
        conn.close()


# ---------- Prioritas 3 — Stable embedding hashing ----------
def test_stable_hash_deterministic():
    """P3-01: _stable_hash harus menghasilkan nilai yang sama untuk input
    yang sama (tidak terpengaruh PYTHONHASHSEED)."""
    import src.brain_embeddings as be

    h1 = be._stable_hash("hello world", 384)
    h2 = be._stable_hash("hello world", 384)
    assert h1 == h2, "Stable hash harus deterministik"

    # Nilai harus dalam range [0, dim)
    assert 0 <= h1 < 384


def test_tfidf_embed_deterministic():
    """P3-02: _tfidf_embed harus menghasilkan vektor yang sama untuk teks
    yang sama (deterministik across restarts)."""
    import src.brain_embeddings as be

    text = "stable embedding hash test"
    v1 = be._tfidf_embed(text)
    v2 = be._tfidf_embed(text)
    assert v1 == v2, "TF-IDF embedding harus deterministik"
    assert len(v1) == be._embedding_dim

    # Vektor nol untuk teks kosong
    v_empty = be._tfidf_embed("")
    assert v_empty == [0.0] * be._embedding_dim


# ---------- Prioritas 4 — Attention-mask pooling ----------
def test_attention_mask_pooling_ignores_padding():
    """P4-01: _mean_pool_with_attention_mask harus mengabaikan token
    padding (attention_mask=0) sehingga vektor tidak terpengaruh oleh
    panjang sequence yang berbeda untuk teks yang sama."""
    torch = pytest.importorskip("torch")

    import src.brain_embeddings as be

    # Simulasi outputs dengan 2 token, dim=4
    class FakeOutputs:
        last_hidden_state = torch.tensor([[[1.0, 0.0, 0.0, 0.0],
                                            [0.0, 1.0, 0.0, 0.0]]])

    # Token 1 aktif, token 2 adalah padding
    mask_active = torch.tensor([[1, 0]])
    mask_both = torch.tensor([[1, 1]])

    pooled_active = be._mean_pool_with_attention_mask(FakeOutputs(), mask_active)
    pooled_both = be._mean_pool_with_attention_mask(FakeOutputs(), mask_both)

    # Hasil dengan padding diabaikan harus sama seperti hanya 1 token aktif
    expected_active = torch.tensor([[1.0, 0.0, 0.0, 0.0]])
    expected_both = torch.tensor([[0.5, 0.5, 0.0, 0.0]])

    assert torch.allclose(pooled_active, expected_active), \
        f"Pooling dengan mask=[1,0] salah: {pooled_active}"
    assert torch.allclose(pooled_both, expected_both), \
        f"Pooling dengan mask=[1,1] salah: {pooled_both}"

    # Kedua hasil harus berbeda (padding memang mempengaruhi bare mean)
    assert not torch.allclose(pooled_active, pooled_both), \
        "Pooling dengan dan tanpa padding harus berbeda"


# ---------- Prioritas 5 — MCP search relevance ----------
def test_search_contacts_ranked_by_relevance(db_mode):
    """P5-01: Kontak yang cocok di nama (title) harus muncul sebelum yang
    cocok hanya di email/notes (body)."""
    conn = db_mode.get_connection()
    try:
        db_mode.upsert_contact(conn, name="John Smith", email="john@example.com",
                               company="Acme Corp", context_notes="likes hiking")
        db_mode.upsert_contact(conn, name="Alice Jones", email="johnson@acme.com",
                               company="Globex", context_notes="john is a colleague")
        db_mode.upsert_contact(conn, name="Bob Wilson", email="bob@test.com",
                               company="Test Co", context_notes="no match here")

        results = db_mode.search_contacts(conn, "john", limit=10)
        assert len(results) >= 2, f"Expected >= 2 results, got {len(results)}"

        # First result should have highest relevance (name match)
        assert results[0]['_relevance'] >= results[1]['_relevance'], \
            f"Relevance not descending: {results[0]['_relevance']} < {results[1]['_relevance']}"
        # John Smith (name match) should rank above johnson@acme.com (email/notes match)
        assert results[0]['name'] == "John Smith", \
            f"Expected John Smith first, got {results[0]['name']}"
    finally:
        conn.close()


def test_search_emails_subject_ranks_higher(db_mode):
    """P5-01: Email yang cocok di subject harus ranking lebih tinggi dari
    yang cocok hanya di body."""
    conn = db_mode.get_connection()
    try:
        db_mode.insert_email(
            conn, message_id="msg-1", thread_id="t1",
            sender="a@test.com", recipient="b@test.com",
            subject="Project Alpha kickoff", body="Just a regular message",
            snippet="", received_at="2024-01-01T00:00:00", labels="[]")
        db_mode.insert_email(
            conn, message_id="msg-2", thread_id="t2",
            sender="c@test.com", recipient="d@test.com",
            subject="Weekly report", body="Discussing Project Alpha details",
            snippet="", received_at="2024-01-02T00:00:00", labels="[]")

        results = db_mode.search_emails(conn, "Alpha", limit=10)
        assert len(results) == 2
        # Subject match (msg-1) should rank higher than body match (msg-2)
        assert results[0]['message_id'] == "msg-1", \
            f"Expected msg-1 first (subject match), got {results[0]['message_id']}"
        assert results[0]['_relevance'] > results[1]['_relevance']
    finally:
        conn.close()


def test_get_upcoming_events_filters_by_query(db_mode):
    """P5-02: get_upcoming_events dengan query harus filter by title/description."""
    conn = db_mode.get_connection()
    try:
        db_mode.upsert_calendar_event(
            conn, event_id="evt-1", title="Team Meeting",
            description="Weekly sync with the team",
            start_time="2026-12-01T10:00:00", end_time="2026-12-01T11:00:00")
        db_mode.upsert_calendar_event(
            conn, event_id="evt-2", title="Lunch",
            description="Project Alpha discussion",
            start_time="2026-12-02T12:00:00", end_time="2026-12-02T13:00:00")
        db_mode.upsert_calendar_event(
            conn, event_id="evt-3", title="Gym",
            description="Workout",
            start_time="2026-12-03T07:00:00", end_time="2026-12-03T08:00:00")

        # Without query → all upcoming events
        all_events = db_mode.get_upcoming_events(conn, limit=10)
        assert len(all_events) == 3

        # With query → only matching events
        alpha_events = db_mode.get_upcoming_events(conn, limit=10, query="Alpha")
        assert len(alpha_events) == 1
        assert alpha_events[0]['event_id'] == "evt-2"

        # With query → title match ranks higher than description match
        team_events = db_mode.get_upcoming_events(conn, limit=10, query="Team")
        assert len(team_events) == 1
        assert team_events[0]['event_id'] == "evt-1"
    finally:
        conn.close()

