# Changelog

All notable changes to Personal Brain are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added
- `pyproject.toml` with project metadata, console entry points, ruff/mypy/coverage config
- Dependabot config for GitHub Actions and pip dependencies
- CI matrix expanded to Windows and macOS (Python 3.11 + 3.12)
- `CHANGELOG.md`

## [1.0.0] — 2026-11-12

### Added
- SQLCipher per-page encryption (AES-256), WAL included — no plaintext temp files
- `migrate_to_sqlcipher.py` — one-time migration from old Fernet `brain.db.enc` format
- Web dashboard (Flask): search, CRUD, graph view, export/import, login
- MCP server: stdio (local) and HTTP over Tailscale (remote), fail-closed auth
- 24/7 sync daemon: notes (mtime), Gmail (pagination), Calendar (syncToken + 410 recovery)
- Obsidian-compatible markdown export/import with `[[backlinks]]` wiki links
- Voice integration: caller detection + contact context
- `manage_brain.py` interactive CLI (local only, no Google/Twitter required)
- Knowledge graph: entities, relations, provenance tracking

### Security
- **Fase 1**: CSRF protection on all POST forms, login rate limiting (5/min), open-redirect fix, secure session cookies (HttpOnly, SameSite=Lax)
- **Fase 2**: MCP API key moved from tool parameter to HTTP header (`Authorization: Bearer` / `X-API-Key`); scope-based keys (`READ` / `WRITE`, write implies read); `hmac.compare_digest` timing-safe comparison; HTTP server refuses to start without a key
- `.gitignore` hardened: excludes `google_token.json`, WAL sidecars (`brain.db-wal`, `brain.db-shm`), `brain.db.enc.bak`, `brain_export/`, daemon state/log, `.pytest_cache`

### Testing
- 33 test cases passing across Python 3.11 and 3.12
- Concurrency regression test: two concurrent SQLCipher connections never lose writes
- Security tests: CSRF reject/accept, open redirect blocked, login rate-limited (429)
- MCP auth scope tests: read key, write key, wrong key, legacy key, header vs parameter precedence

### Changed
- `requirements.txt` split into runtime / dev / embeddings
- `.env.example`: `BRAIN_API_KEY` added, `BRAIN_NOTES_FOLDER` default `./Notes`
- `manage_brain.py`: default notes folder relative to project root
- `migrate_to_sqlcipher.py`: docstring updated (`sqlcipher3-binary`)
- Removed dead `python_packages/` PyYAML stub

### Removed
- `src/brain_encryption.py` (dead Fernet whole-file architecture, replaced by SQLCipher)

[0.9.0] — 2026-11-11

### Added
- Phase 3g: Export markdown with backlinks (`index.md`, `graph.md`, `contacts/`, `knowledge/`, `notes/`, `entities/`)
- Phase 3e: 24/7 sync daemon with state persistence
- Phase 3f: Remote MCP via Tailscale
- Web dashboard Jinja2 templates (13 files)
- Access control: `users` / `item_shares` tables

### Fixed
- Dashboard NameError on 4 routes (`/search`, `/graph`, `/import`, `/conversations`)
- Calendar `syncToken` + `orderBy` incompatibility; 410 GONE recovery
- Gmail date comparison + pagination (`nextPageToken`)

[0.8.0] — 2026-11-10

### Added
- Phase 2: `brain_sync_daemon.py`, `brain_mcp_server.py`, `brain_voice.py`
- SQLite database with contacts, notes, knowledge, entities, relations
- Semantic search with TF-IDF fallback
- `.env.example`, `README.md`, `.gitignore`

[0.7.0] — 2026-11-09

### Added
- Initial project structure
- `brain_db.py` SQLite schema + CRUD
- `brain_connectors.py` Gmail, Calendar, Twitter/X, markdown notes
- `brain_integration.py` agent read/write flow
- `manage_brain.py` CLI
