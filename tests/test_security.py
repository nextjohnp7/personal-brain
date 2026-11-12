import re


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
