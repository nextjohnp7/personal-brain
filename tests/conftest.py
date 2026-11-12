import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "src"))

import pytest


@pytest.fixture()
def dashboard(monkeypatch, tmp_path):
    """Isolated app: empty temp DB, auth off (repo default)."""
    monkeypatch.delenv("BRAIN_DB_KEY", raising=False)  # plain SQLite mode

    import brain_db
    monkeypatch.setattr(brain_db, "DB_PATH", tmp_path / "brain_test.db")
    monkeypatch.setattr(brain_db, "ENCRYPTED_PATH", tmp_path / "brain_test.db.enc")
    # Deterministic embeddings: no model download, no per-process hash()
    monkeypatch.setattr(brain_db, "embed_text", lambda text: [float(len(text) % 7)] * 8)

    import brain_web_dashboard as dash
    monkeypatch.setattr(dash, "AUTH_ENABLED", False)
    monkeypatch.setattr(dash, "BRAIN_API_KEY", "")
    dash.app.config["TESTING"] = True
    return dash


@pytest.fixture()
def client(dashboard):
    return dashboard.app.test_client()


@pytest.fixture()
def auth_dashboard(dashboard, monkeypatch):
    from werkzeug.security import generate_password_hash
    monkeypatch.setattr(dashboard, "AUTH_ENABLED", True)
    monkeypatch.setattr(dashboard, "DASHBOARD_USER", "tester")
    monkeypatch.setattr(dashboard, "DASHBOARD_PASSWORD_HASH",
                        generate_password_hash("rahasia-test-123"))
    return dashboard


@pytest.fixture()
def logged_in_client(auth_dashboard):
    c = auth_dashboard.app.test_client()
    resp = c.post("/login", data={"username": "tester", "password": "rahasia-test-123"})
    assert resp.status_code == 302
    return c
