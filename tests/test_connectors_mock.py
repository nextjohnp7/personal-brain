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


class FakeEventGet:
    def __init__(self, event_data):
        self._event_data = event_data

    def execute(self):
        return self._event_data


class FakeMessagesList:
    def __init__(self, pages):
        self._pages = pages
        self._index = 0

    def execute(self):
        page = self._pages[self._index]
        self._index += 1
        return page


class FakeProfileGet:
    def __init__(self, data):
        self._data = data

    def execute(self):
        return self._data


class FakeHistoryList:
    def __init__(self, pages):
        self._pages = pages
        self._index = 0

    def execute(self):
        page = self._pages[self._index]
        self._index += 1
        return page


class FakeHistoryResource:
    def __init__(self, users):
        self._users = users

    def list(self, **kwargs):
        self._users._messages_calls += 1
        return FakeHistoryList([{"history": [], "nextPageToken": None}])


class FakeUsers:
    def __init__(self, messages_pages, events_pages=None, http_error_410=False):
        self._messages_pages = messages_pages
        self._events_pages = events_pages or []
        self._events_index = 0
        self._http_error_410 = http_error_410
        self._messages_calls = 0
        self._events_calls = 0
        self._captured_q = None
        self._messages_resource = None
        self._events_resource = None

    def messages(self):
        if self._messages_resource is None:
            self._messages_resource = FakeMessagesResource(self)
        return self._messages_resource

    def events(self):
        if self._events_resource is None:
            self._events_resource = FakeEventsResource(self)
        return self._events_resource

    def history(self):
        return FakeHistoryResource(self)

    def getProfile(self, **kwargs):
        return FakeProfileGet({"historyId": "1"})

    @property
    def captured_q(self):
        return self._captured_q


class FakeMessagesResource:
    def __init__(self, users):
        self._users = users
        self._list = None

    def list(self, **kwargs):
        self._users._messages_calls += 1
        self._users._captured_q = kwargs.get("q", "")
        if self._list is None:
            self._list = FakeMessagesList(self._users._messages_pages)
        return self._list

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
            resp = type("Response", (), {"status": 410, "reason": "syncToken invalid"})()
            raise HttpError(resp, b'{"error": {"code": 410, "message": "syncToken invalid"}}')
        page = self._pages[self._index]
        self._index += 1
        return page


class FakeEventsResource:
    def __init__(self, users):
        self._users = users
        self._list = None

    def list(self, **kwargs):
        self._users._events_calls += 1
        self._users._captured_page_token = kwargs.get("pageToken", None)
        if self._list is None:
            self._list = FakeEventsList(self._users._events_pages, self._users._http_error_410)
        return self._list

    def get(self, **kwargs):
        event_id = kwargs.get("eventId", kwargs.get("id", "unknown"))
        return FakeEventGet({"id": event_id, "summary": f"Event {event_id}"})


class FakeService:
    def __init__(self, messages_pages, events_pages=None, http_error_410=False):
        self._users = FakeUsers(messages_pages, events_pages, http_error_410)
        self._events_resource = None

    def users(self):
        return self._users

    def events(self):
        if self._events_resource is None:
            self._events_resource = FakeEventsResource(self._users)
        return self._events_resource


def fake_build(api_name, version, credentials=None, **kwargs):
    """Fake googleapiclient.discovery.build. Routes by api_name."""
    if api_name == "gmail":
        service = FakeService(
            getattr(fake_build, "_gmail_pages", []),
            http_error_410=getattr(fake_build, "_gmail_410", False),
        )
        fake_build._last_service = service
        return service
    elif api_name == "calendar":
        service = FakeService(
            [],
            getattr(fake_build, "_calendar_pages", []),
            http_error_410=getattr(fake_build, "_calendar_410", False),
        )
        fake_build._last_service = service
        return service
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
    monkeypatch.setenv("BRAIN_DB_KEY", "nydE-s2-6NLtiR_lb8dsM_rlGw1wTxMfWPn2YAeKOcA=")  # test key, encrypted mode

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
    monkeypatch.setenv("BRAIN_DB_KEY", "nydE-s2-6NLtiR_lb8dsM_rlGw1wTxMfWPn2YAeKOcA=")  # test key, encrypted mode

    conn = db_mode.get_connection()
    try:
        state = {"gmail_last_sync": "2026-10-20T10:00:00+00:00"}
        bsd.sync_gmail_once(conn, "dummy-creds", state)
    finally:
        conn.close()

    assert fake_build._last_service._users.captured_q.startswith("after:")


# ---------- Calendar sync with mock ----------

from brain_sync_daemon import sync_calendar_once


def test_calendar_sync_410_recovery(db_mode, monkeypatch):
    """Calendar 410 GONE triggers syncToken clear and full resync."""
    import googleapiclient.discovery
    import brain_sync_daemon as bsd

    fake_build._calendar_pages = [{"items": []}, {"items": []}]
    fake_build._calendar_410 = True

    monkeypatch.setattr(googleapiclient.discovery, "build", fake_build)
    monkeypatch.setenv("BRAIN_DB_KEY", "nydE-s2-6NLtiR_lb8dsM_rlGw1wTxMfWPn2YAeKOcA=")  # test key, encrypted mode

    conn = db_mode.get_connection()
    try:
        state = {"calendar_sync_token": "old-stale-token"}
        stats = bsd.sync_calendar_once(conn, "dummy-creds", state)
    finally:
        conn.close()

    assert state.get("calendar_sync_token") is None
    assert fake_build._last_service._users._events_calls == 2


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
        {"items": [], "nextSyncToken": "sync_token_after_resync"},
    ]
    fake_build._calendar_pages = pages
    fake_build._calendar_410 = False

    monkeypatch.setattr(googleapiclient.discovery, "build", fake_build)
    monkeypatch.setenv("BRAIN_DB_KEY", "nydE-s2-6NLtiR_lb8dsM_rlGw1wTxMfWPn2YAeKOcA=")  # test key, encrypted mode

    conn = db_mode.get_connection()
    try:
        state = {}
        stats = bsd.sync_calendar_once(conn, "dummy-creds", state)
    finally:
        conn.close()

    assert stats["events"] >= 1
    assert state.get("calendar_sync_token") is not None


# ---------- Prioritas 6 — Calendar pagination recovery ----------
def test_calendar_page_token_persistence(db_mode, monkeypatch):
    """P6-01: calendar_page_token is saved in state after each page and
    used to resume on next run. Upsert makes re-processing safe, but
    pageToken persistence avoids redundant API calls on recovery."""
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
        {
            "items": [],
            "nextSyncToken": "final_sync_token",
        },
    ]
    fake_build._calendar_pages = pages
    fake_build._calendar_410 = False

    monkeypatch.setattr(googleapiclient.discovery, "build", fake_build)
    monkeypatch.setenv("BRAIN_DB_KEY", "nydE-s2-6NLtiR_lb8dsM_rlGw1wTxMfWPn2YAeKOcA=")

    conn = db_mode.get_connection()
    try:
        # Simulate resume from interrupted sync: state has saved pageToken
        state = {"calendar_page_token": "page2"}
        stats = bsd.sync_calendar_once(conn, "dummy-creds", state)
    finally:
        conn.close()

    # Mock should have received the saved pageToken
    captured = fake_build._last_service._users._captured_page_token
    assert captured == "page2", f"Expected pageToken 'page2', got {captured!r}"

    # Should have processed page 2 and saved final syncToken
    assert state.get("calendar_sync_token") == "final_sync_token"
    # Page token cleared after full pagination
    assert state.get("calendar_page_token") is None
    assert stats["events"] >= 1


def test_calendar_page_token_saved_during_pagination(db_mode, monkeypatch):
    """P6-02: calendar_page_token is saved in state after each page."""
    import googleapiclient.discovery
    import brain_sync_daemon as bsd

    # Monkeypatch save_state to capture state after each page
    saved_states = []
    original_save_state = bsd.save_state

    def capture_save_state(state):
        saved_states.append(dict(state))
        original_save_state(state)

    monkeypatch.setattr(bsd, "save_state", capture_save_state)

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
        {
            "items": [],
            "nextSyncToken": "final_sync_token",
        },
    ]
    fake_build._calendar_pages = pages
    fake_build._calendar_410 = False

    monkeypatch.setattr(googleapiclient.discovery, "build", fake_build)
    monkeypatch.setenv("BRAIN_DB_KEY", "nydE-s2-6NLtiR_lb8dsM_rlGw1wTxMfWPn2YAeKOcA=")

    conn = db_mode.get_connection()
    try:
        state = {}
        stats = bsd.sync_calendar_once(conn, "dummy-creds", state)
    finally:
        conn.close()

    # At least one saved state should contain calendar_page_token
    page_token_states = [
        s for s in saved_states if s.get("calendar_page_token") == "page2"
    ]
    assert len(page_token_states) >= 1, \
        f"calendar_page_token 'page2' not found in saved states: {saved_states}"
    # Final state should have pageToken cleared
    assert state.get("calendar_page_token") is None


# ---------- Prioritas 0 — Calendar failed-event retry ----------
def test_calendar_failed_event_recorded_and_retried(db_mode, monkeypatch):
    """P0-01: Calendar event yang gagal diproses dicatat ke
    calendar_failed_events dan di-retry pada run berikutnya."""
    import googleapiclient.discovery
    import brain_sync_daemon as bsd
    import src.brain_db as brain_db
    from brain_db import get_failed_calendar_events, clear_failed_calendar_event

    monkeypatch.setenv("BRAIN_DB_KEY", "nydE-s2-6NLtiR_lb8dsM_rlGw1wTxMfWPn2YAeKOcA=")

    # Force upsert_calendar_event to raise so event is recorded as failed
    original_upsert = brain_db.upsert_calendar_event
    call_count = {"n": 0}

    def failing_upsert(*args, **kwargs):
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise RuntimeError("simulated processing failure")
        return original_upsert(*args, **kwargs)

    monkeypatch.setattr(bsd, "upsert_calendar_event", failing_upsert)

    pages = [
        {
            "items": [
                {
                    "id": "evt-fail-1",
                    "summary": "Failing Event",
                    "start": {"dateTime": "2026-10-21T10:00:00Z"},
                    "end": {"dateTime": "2026-10-21T11:00:00Z"},
                    "status": "confirmed",
                }
            ],
            "nextSyncToken": "sync_token_1",
        },
    ]
    fake_build._calendar_pages = pages
    fake_build._calendar_410 = False

    monkeypatch.setattr(googleapiclient.discovery, "build", fake_build)

    conn = db_mode.get_connection()
    try:
        state = {}
        stats = bsd.sync_calendar_once(conn, "dummy-creds", state)

        # Event should be in failed table (queried BEFORE close)
        failed = get_failed_calendar_events(conn)
        assert len(failed) >= 1, f"Expected failed event recorded, got {failed}"
        assert failed[0]["event_id"] == "evt-fail-1"
        assert failed[0]["retry_count"] >= 1
    finally:
        conn.close()


def test_calendar_failed_event_retry_succeeds(db_mode, monkeypatch):
    """P0-02: Calendar event yang gagal sebelumnya berhasil di-retry
    dan dihapus dari failed table."""
    import googleapiclient.discovery
    import brain_sync_daemon as bsd
    import src.brain_db as brain_db
    from brain_db import (
        get_failed_calendar_events,
        record_failed_calendar_event,
    )

    monkeypatch.setenv("BRAIN_DB_KEY", "nydE-s2-6NLtiR_lb8dsM_rlGw1wTxMfWPn2YAeKOcA=")

    conn = db_mode.get_connection()
    try:
        record_failed_calendar_event(conn, "evt-retry-1", "transient error")
    finally:
        conn.close()

    # Mock: events().get() returns the event successfully, upsert works
    pages = [{"items": [], "nextSyncToken": "sync_token_2"}]
    fake_build._calendar_pages = pages
    fake_build._calendar_410 = False

    monkeypatch.setattr(googleapiclient.discovery, "build", fake_build)

    conn = db_mode.get_connection()
    try:
        state = {}
        stats = bsd.sync_calendar_once(conn, "dummy-creds", state)

        # Failed event should be cleared after successful retry
        failed = get_failed_calendar_events(conn)
        retry_ids = [f["event_id"] for f in failed]
        assert "evt-retry-1" not in retry_ids, \
            f"Failed event should be cleared after retry, still in {retry_ids}"
    finally:
        conn.close()


def test_calendar_failed_event_max_retry(db_mode, monkeypatch):
    """P2-01: Calendar event yang melebihi max retry (5) dilewati,
    tidak dicoba ulang selamanya."""
    import googleapiclient.discovery
    import brain_sync_daemon as bsd
    import src.brain_db as brain_db
    from brain_db import (
        record_failed_calendar_event,
        get_failed_calendar_events,
    )

    monkeypatch.setenv("BRAIN_DB_KEY", "nydE-s2-6NLtiR_lb8dsM_rlGw1wTxMfWPn2YAeKOcA=")

    conn = db_mode.get_connection()
    try:
        record_failed_calendar_event(conn, "evt-max-retry", "persistent error")
        conn.execute(
            "UPDATE calendar_failed_events SET retry_count = 5 WHERE event_id = ?",
            ("evt-max-retry",),
        )
        conn.commit()
    finally:
        conn.close()

    pages = [{"items": [], "nextSyncToken": "sync_token_3"}]
    fake_build._calendar_pages = pages
    fake_build._calendar_410 = False

    monkeypatch.setattr(googleapiclient.discovery, "build", fake_build)

    conn = db_mode.get_connection()
    try:
        state = {}
        stats = bsd.sync_calendar_once(conn, "dummy-creds", state)

        # Event should still be in failed table (skipped, not retried)
        failed = get_failed_calendar_events(conn)
        retry_ids = [f["event_id"] for f in failed]
        assert "evt-max-retry" in retry_ids, \
            f"Event at max retry should remain in failed table, got {retry_ids}"
    finally:
        conn.close()


# ---------- Prioritas 1 — Gmail retry double-count ----------
def test_gmail_retry_no_double_count(db_mode, monkeypatch):
    """P1-01: Gmail retry yang berhasil tidak boleh menaikkan
    new_messages dua kali (_process_gmail_message sudah menambahkannya)."""
    import googleapiclient.discovery
    import brain_sync_daemon as bsd
    import src.brain_db as brain_db
    from brain_db import record_failed_gmail_message

    monkeypatch.setenv("BRAIN_DB_KEY", "nydE-s2-6NLtiR_lb8dsM_rlGw1wTxMfWPn2YAeKOcA=")

    conn = db_mode.get_connection()
    try:
        record_failed_gmail_message(conn, "msg-retry-1", "transient error")
    finally:
        conn.close()

    pages = [{"messages": [], "nextPageToken": None}]
    fake_build._gmail_pages = pages
    fake_build._gmail_410 = False

    monkeypatch.setattr(googleapiclient.discovery, "build", fake_build)

    conn = db_mode.get_connection()
    try:
        state = {"gmail_history_id": "1"}
        stats = bsd.sync_gmail_once(conn, "dummy-creds", state)

        # new_messages should be exactly 1 (not 2)
        assert stats["new_messages"] == 1, \
            f"Expected new_messages=1, got {stats['new_messages']} (double-count bug?)"
    finally:
        conn.close()


# ---------- Prioritas 1 — Calendar initial sync coverage ----------
def test_calendar_initial_sync_uses_time_window(db_mode, monkeypatch):
    """P1-02: Calendar initial sync harus menggunakan timeMin/timeMax
    (cakupan waktu event), bukan updatedMin 30 hari, agar event masa depan
    yang dibuat lama tidak terlewat."""
    import googleapiclient.discovery
    import brain_sync_daemon as bsd

    monkeypatch.setenv("BRAIN_DB_KEY", "nydE-s2-6NLtiR_lb8dsM_rlGw1wTxMfWPn2YAeKOcA=")

    captured_kwargs = {}

    class CapturingEventsList:
        def __init__(self, pages, http_error_410=False):
            self._pages = pages
            self._index = 0

        def execute(self):
            page = self._pages[self._index]
            self._index += 1
            return page

    class CapturingEventsResource:
        def __init__(self, users):
            self._users = users

        def list(self, **kwargs):
            captured_kwargs.update(kwargs)
            return CapturingEventsList([{"items": [], "nextSyncToken": "tok"}])

        def get(self, **kwargs):
            return FakeEventGet({"id": kwargs.get("eventId", "x")})

    class CapturingUsers:
        def __init__(self):
            self._events_resource = CapturingEventsResource(self)

        def events(self):
            return self._events_resource

        def getProfile(self, **kwargs):
            return FakeProfileGet({"historyId": "1"})

    class CapturingService:
        def __init__(self):
            self._users = CapturingUsers()

        def users(self):
            return self._users

        def events(self):
            return self._users.events()

    def capturing_build(api_name, version, credentials=None, **kwargs):
        service = CapturingService()
        fake_build._last_service = service
        return service

    monkeypatch.setattr(googleapiclient.discovery, "build", capturing_build)

    conn = db_mode.get_connection()
    try:
        state = {}
        stats = bsd.sync_calendar_once(conn, "dummy-creds", state)

        # Initial sync should use timeMin/timeMax, NOT updatedMin
        assert "timeMin" in captured_kwargs, \
            f"Expected timeMin in initial sync kwargs, got {list(captured_kwargs.keys())}"
        assert "timeMax" in captured_kwargs, \
            f"Expected timeMax in initial sync kwargs, got {list(captured_kwargs.keys())}"
        assert "updatedMin" not in captured_kwargs, \
            f"updatedMin should NOT be used for initial sync, got {captured_kwargs}"
    finally:
        conn.close()
