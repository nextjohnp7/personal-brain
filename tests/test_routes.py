import pytest

PAGES = ["/", "/contacts", "/contacts/add", "/notes", "/notes/add",
         "/search", "/search?q=alice", "/graph", "/graph?q=alice",
         "/import", "/conversations"]


@pytest.mark.parametrize("path", PAGES)
def test_page_renders(client, path):
    """Regression guard: every page must render (no NameError/500)."""
    resp = client.get(path)
    assert resp.status_code == 200, f"GET {path} -> {resp.status_code}"


def test_add_contact_roundtrip(client):
    resp = client.post("/contacts/add",
                       data={"name": "Alice Tester", "email": "alice@example.com"})
    assert resp.status_code == 302
    assert b"Alice Tester" in client.get("/contacts").data


def test_contact_name_is_html_escaped(client):
    client.post("/contacts/add", data={"name": "<script>alert(1)</script>"})
    assert b"<script>alert(1)</script>" not in client.get("/contacts").data


# ---- Auth (guards the /export + /import fix) ----

def test_redirect_to_login_when_auth_enabled(auth_dashboard):
    c = auth_dashboard.app.test_client()
    resp = c.get("/contacts")
    assert resp.status_code == 302
    assert "/login" in resp.headers["Location"]


def test_export_requires_login(auth_dashboard):
    c = auth_dashboard.app.test_client()
    assert c.get("/export").status_code == 302  # must NOT leak without login


def test_login_then_export_json(logged_in_client):
    resp = logged_in_client.get("/export?format=json")
    assert resp.status_code == 200
    assert "contacts" in resp.get_json()


def test_login_wrong_password(auth_dashboard):
    c = auth_dashboard.app.test_client()
    resp = c.post("/login", data={"username": "tester", "password": "salah"})
    assert resp.status_code == 200            # back to login form
    assert c.get("/contacts").status_code == 302  # still locked out


# ---- API key ----

def test_api_key_enforced(dashboard, monkeypatch):
    monkeypatch.setattr(dashboard, "BRAIN_API_KEY", "kunci-rahasia")
    c = dashboard.app.test_client()
    assert c.get("/api/search?q=x").status_code == 401
    assert c.get("/api/search?q=x", headers={"X-API-Key": "salah"}).status_code == 401
    ok = c.get("/api/search?q=x", headers={"X-API-Key": "kunci-rahasia"})
    assert ok.status_code == 200
