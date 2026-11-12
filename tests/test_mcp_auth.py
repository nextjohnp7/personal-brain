"""Tests for MCP server auth: header-based API key + scope enforcement."""

import brain_mcp_server


def test_no_auth_configured_denies_all(monkeypatch):
    """Fail-closed: when no API key is set, all scopes are denied."""
    monkeypatch.setattr(brain_mcp_server, "EFFECTIVE_READ_KEY", "")
    monkeypatch.setattr(brain_mcp_server, "EFFECTIVE_WRITE_KEY", "")
    err = brain_mcp_server._check_mcp_api_key("", scope="read")
    assert err is not None
    assert "not configured" in err
    err = brain_mcp_server._check_mcp_api_key("", scope="write")
    assert err is not None
    assert "not configured" in err


def test_missing_key_when_auth_configured(monkeypatch):
    """When auth is configured but no key provided → denied."""
    monkeypatch.setattr(brain_mcp_server, "EFFECTIVE_READ_KEY", "read-key-123")
    monkeypatch.setattr(brain_mcp_server, "EFFECTIVE_WRITE_KEY", "")
    err = brain_mcp_server._check_mcp_api_key("", scope="read")
    assert err is not None
    assert "Missing MCP API key" in err


def test_read_key_allows_read_tools(monkeypatch):
    monkeypatch.setattr(brain_mcp_server, "EFFECTIVE_READ_KEY", "read-key-123")
    monkeypatch.setattr(brain_mcp_server, "EFFECTIVE_WRITE_KEY", "")
    assert brain_mcp_server._check_mcp_api_key("read-key-123", scope="read") is None


def test_read_key_denies_write_tools(monkeypatch):
    monkeypatch.setattr(brain_mcp_server, "EFFECTIVE_READ_KEY", "read-key-123")
    monkeypatch.setattr(brain_mcp_server, "EFFECTIVE_WRITE_KEY", "")
    err = brain_mcp_server._check_mcp_api_key("read-key-123", scope="write")
    assert err is not None
    assert "write scope required" in err


def test_write_key_allows_read_and_write(monkeypatch):
    monkeypatch.setattr(brain_mcp_server, "EFFECTIVE_READ_KEY", "")
    monkeypatch.setattr(brain_mcp_server, "EFFECTIVE_WRITE_KEY", "write-key-456")
    assert brain_mcp_server._check_mcp_api_key("write-key-456", scope="read") is None
    assert brain_mcp_server._check_mcp_api_key("write-key-456", scope="write") is None


def test_wrong_key_denied(monkeypatch):
    monkeypatch.setattr(brain_mcp_server, "EFFECTIVE_READ_KEY", "correct-key")
    monkeypatch.setattr(brain_mcp_server, "EFFECTIVE_WRITE_KEY", "")
    err = brain_mcp_server._check_mcp_api_key("wrong-key", scope="read")
    assert err is not None


def test_param_key_takes_precedence_over_header():
    """When both param and header are set, parameter wins (stdio compatibility)."""
    brain_mcp_server._mcp_api_key_ctx.set("header-key-789")
    assert brain_mcp_server._get_effective_key("param-key") == "param-key"
    brain_mcp_server._mcp_api_key_ctx.set("")


def test_header_key_used_when_no_param():
    """Header context var is used when no parameter provided."""
    brain_mcp_server._mcp_api_key_ctx.set("header-key-789")
    assert brain_mcp_server._get_effective_key("") == "header-key-789"
    brain_mcp_server._mcp_api_key_ctx.set("")


def test_legacy_key_works_as_both_scopes(monkeypatch):
    """Legacy BRAIN_MCP_API_KEY acts as both read and write key."""
    monkeypatch.setattr(brain_mcp_server, "EFFECTIVE_READ_KEY", "legacy-key")
    monkeypatch.setattr(brain_mcp_server, "EFFECTIVE_WRITE_KEY", "legacy-key")
    assert brain_mcp_server._check_mcp_api_key("legacy-key", scope="read") is None
    assert brain_mcp_server._check_mcp_api_key("legacy-key", scope="write") is None


def test_hmac_compare_used_not_plain_equality(monkeypatch):
    """Scope check uses hmac.compare_digest, not != (timing-safe)."""
    import hmac

    monkeypatch.setattr(brain_mcp_server, "EFFECTIVE_READ_KEY", "correct-key")
    monkeypatch.setattr(brain_mcp_server, "EFFECTIVE_WRITE_KEY", "")
    # Wrong key must be rejected
    assert brain_mcp_server._check_mcp_api_key("wrong-key", scope="read") is not None
    # Correct key must pass
    assert brain_mcp_server._check_mcp_api_key("correct-key", scope="read") is None
    # Verify hmac.compare_digest is the mechanism (not !=)
    assert hmac.compare_digest("correct-key", "correct-key")
    assert not hmac.compare_digest("wrong-key", "correct-key")
