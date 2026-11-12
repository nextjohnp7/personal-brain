# Changelog

## [0.1.0] — 2026-11-12

### Added
- SQLCipher per-page encryption (AES-256) with WAL mode — no plaintext temp files
- `migrate_to_sqlcipher.py` — one-time migration from old Fernet `brain.db.enc`
- Web dashboard (Flask): search, CRUD, graph view, export/import, login
- MCP server: stdio (local) and HTTP over Tailscale (remote), fail-closed auth
- MCP auth: header-based API key (`Authorization: Bearer` / `X-API-Key`), scope-based keys (READ / WRITE)
- 24/7 sync daemon: notes (mtime), Gmail (pagination), Calendar (syncToken + 410 recovery)
- Obsidian-compatible markdown export/import with `[[backlinks]]` wiki links
- Voice integration: caller detection + contact context
- `manage_brain.py` interactive CLI (local only)
- Knowledge graph: entities, relations, provenance tracking

### Security (Fase 1 & 2)
- CSRF protection on all POST forms
- Login rate limiting (5/minute)
- Open-redirect fix on login `next` parameter
- Secure session cookies (HttpOnly, SameSite=Lax)
- MCP API key moved from tool parameter to HTTP header
- `hmac.compare_digest` for timing-safe key comparison
- HTTP MCP server refuses to start without API key

### Testing
- 33 test cases passing on Python 3.11 and 3.12
- Concurrency regression test: two concurrent SQLCipher connections never lose writes
- Security tests: CSRF, open redirect, login rate limit, MCP auth scope

### CI
- GitHub Actions: Ubuntu × Python 3.11/3.12
- Ruff, mypy, coverage (non-blocking)
