"""Mock tests for sync connectors (Gmail pagination, Calendar 410 recovery,
date parsing, email/name extraction).

Uses fake Google API service objects instead of real HTTP calls.
Mocks googleapiclient.discovery.build since it is imported locally inside
the sync functions.
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


# ---------- Helper: fake Google API service ----------

class FakeMessageGet:
    def __init__(self, msg_data):
        self._msg_data = msg_data

    def execute(self):
        return self._msg_data


class FakeMessagesList:
    def __init__(self, pages):
        self._pages = pages
        self._index = 0

    def execute(self):
        page = self._pages[self._index]
        self._index += 1
        return page


class FakeUsers:
    def __init__(self, messages_pages, events_pages=None, http_error_410=False):
        self._messages_pages = messages_pages
        self._events_pages = events_pages or []
        self._events_index = 0
        self._http_error_410 = http_error_410
        self._messages_calls = 0
        self._events_calls = 0
        self._captured_q = None

    def messages(self):
        return FakeMessagesResource(self)

    def events(self):
        return FakeEventsResource(self)

    @property
    def captured_q(self):
        return self._captured_q


class FakeMessagesResource:
    def __init__(self, users):
        self._users = users

    def list(self, **kwargs):
        self._users._messages_calls += 1
        self._users._captured_q = kwargs.get("q", "")
        return FakeMessagesList(self._users._messages_pages)

    def get(self, **kwargs):
        msg_id = kwargs.get("id", "unknown")
        return FakeMessageGet({
            "id": msg_id,
            "threadId": "thread_" + msg_id,
            "payload": {
                "headers": [
                    {"name": "From", "value": "Sender Name <sender@example.com>"},
                    {"name": "To", "value": "me@example.com"},
                    {"name": "Subject", "value": "Test Subject"},
                    {"name": "Date", "value": "Wed, 21 Oct 2026 10:00:00 +0000"},
                ]
            },
            "snippet": "Test snippet",
            "labelIds": ["INBOX"],
        })


class FakeEventsList:
    def __init__(self, pages, http_error_410=False):
        self._pages = pages
        self._index = 0
        self._http_error_410 = http_error_410
        self._retried = False

    def execute(self):
        if self._http_error_410 and not self._retried:
            self._retried = True
            from googleapiclient.errors import HttpError
            resp = type("Response", (), {"status": 410})()
            raise HttpError(resp, b'{"error": {"code": 410, "message": "syncToken invalid"}}')
        page = self._pages[self._index]
        self._index += 1
        return page


class FakeEventsResource:
    def __init__(self, users):
        self._users = users

    def list(self, **kwargs):
        self._users._events_calls += 1
        return FakeEventsList(self._users._events_pages, self._users._http_error_410)


class FakeService:
    def __init__(self, messages_pages, events_pages=None, http_error_410=False):
        self._users = FakeUsers(messages_pages, events_pages, http_error_410)

    def users(self):
        return self._users

    def events(self):
        return self._users


def fake_build(api_name, version, credentials=None, **kwargs):
    """Fake googleapiclient.discovery.build. Routes by api_name."""
    if api_name == "gmail":
        return FakeService(
            getattr(fake_build, "_gmail_pages", []),
            http_error_410=getattr(fake_build, "_gmail_410", False),
        )
    elif api_name == "calendar":
        return FakeService(
            [],
            getattr(fake_build, "_calendar_pages", []),
            http_error_410=getattr(fake_build, "_calendar_410", False),
        )
    raise ValueError(f"Unknown API: {api_name}")


# ---------- Extract helpers ----------

from brain_connectors import _extract_email, _extract_name


def test_extract_email_with_brackets():
    assert _extract_email("Name <email@domain.com>") == "email@domain.com"


def test_extract_email_plain():
    assert _extract_email("email@domain.com") == "email@domain.com"


def test_extract_email_empty():
    assert _extract_email("") is None
    assert _extract_email(None) is None


def test_extract_name_with_brackets():
    assert _extract_name('"John Doe" <john@example.com>') == "John Doe"


def test_extract_name_no_brackets():
    assert _extract_name("just email@example.com") is None


# ---------- Gmail sync with mock ----------

from brain_sync_daemon import sync_gmail_once


def test_gmail_sync_pagination(db_mode, monkeypatch):
    """Gmail sync follows nextPageToken across multiple pages."""
    import googleapiclient.discovery
    import brain_sync_daemon as bsd

    pages = [
        {"messages": [{"id": "1"}, {"id": "2"}], "nextPageToken": "page2"},
        {"messages": [{"id": "3"}]},
    ]
    fake_build._gmail_pages = pages
    fake_build._gmail_410 = False

    monkeypatch.setattr(googleapiclient.discovery, "build", fake_build)
    monkeypatch.setenv("BRAIN_DB_KEY", "")

    conn = db_mode.get_connection()
    try:
        state = {}
        stats = bsd.sync_gmail_once(conn, "dummy-creds", state)
    finally:
        conn.close()

    assert stats["emails"] == 3
    assert stats["new_messages"] == 3
    assert stats["contacts"] == 3


def test_gmail_sync_date_query(db_mode, monkeypatch):
    """Gmail sync passes 'after:' query when state has last sync timestamp."""
    import googleapiclient.discovery
    import brain_sync_daemon as bsd

    fake_build._gmail_pages = [{"messages": []}]
    fake_build._gmail_410 = False

    monkeypatch.setattr(googleapiclient.discovery, "build", fake_build)
    monkeypatch.setenv("BRAIN_DB_KEY", "")

    conn = db_mode.get_connection()
    try:
        state = {"gmail_last_sync": "2026-10-20T10:00:00+00:00"}
        bsd.sync_gmail_once(conn, "dummy-creds", state)
    finally:
        conn.close()

    assert fake_build._users.captured_q.startswith("after:")


# ---------- Calendar sync with mock ----------

from brain_sync_daemon import sync_calendar_once


def test_calendar_sync_410_recovery(db_mode, monkeypatch):
    """Calendar 410 GONE triggers syncToken clear and full resync."""
    import googleapiclient.discovery
    import brain_sync_daemon as bsd

    fake_build._calendar_pages = [{"items": []}, {"items": []}]
    fake_build._calendar_410 = True

    monkeypatch.setattr(googleapiclient.discovery, "build", fake_build)
    monkeypatch.setenv("BRAIN_DB_KEY", "")

    conn = db_mode.get_connection()
    try:
        state = {"calendar_sync_token": "old-stale-token"}
        stats = bsd.sync_calendar_once(conn, "dummy-creds", state)
    finally:
        conn.close()

    assert state.get("calendar_sync_token") is None
    assert fake_build._users._events_calls == 2


def test_calendar_sync_pagination(db_mode, monkeypatch):
    """Calendar sync follows nextPageToken across multiple pages."""
    import googleapiclient.discovery
    import brain_sync_daemon as bsd

    pages = [
        {
            "items": [
                {
                    "id": "evt1",
                    "summary": "Event 1",
                    "start": {"dateTime": "2026-10-21T10:00:00Z"},
                    "end": {"dateTime": "2026-10-21T11:00:00Z"},
                    "attendees": [{"email": "attendee@example.com"}],
                    "status": "confirmed",
                }
            ],
            "nextPageToken": "page2",
        },
        {"items": []},
    ]
    fake_build._calendar_pages = pages
    fake_build._calendar_410 = False

    monkeypatch.setattr(googleapiclient.discovery, "build", fake_build)
    monkeypatch.setenv("BRAIN_DB_KEY", "")

    conn = db_mode.get_connection()
    try:
        state = {}
        stats = bsd.sync_calendar_once(conn, "dummy-creds", state)
    finally:
        conn.close()

    assert stats["events"] >= 1
    assert state.get("calendar_sync_token") is not None
