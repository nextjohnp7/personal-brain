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
