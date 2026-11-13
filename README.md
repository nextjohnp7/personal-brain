# 🧠 Personal Brain

A local-first knowledge base ("second brain") for AI agents. It syncs your notes,
Gmail, Google Calendar, and contacts into a single encrypted SQLite database, and
exposes it to agents via the Model Context Protocol (MCP), a web dashboard, and a
voice-call context helper.

All data stays on your machine. The database is encrypted at rest with SQLCipher
(AES-256, per-page). No server, no cloud.

[![tests](https://github.com/nextjohnp7/personal-brain/actions/workflows/tests.yml/badge.svg)](https://github.com/nextjohnp7/personal-brain/actions/workflows/tests.yml)

## Features

- **Connectors** — Gmail, Google Calendar, Twitter/X, and a local markdown notes folder
- **Knowledge graph** — entities and relations (`works_at`, `knows`, `met_with`, …) with provenance tracking
- **Semantic search** — sentence-transformers embeddings with automatic TF-IDF fallback
- **Web dashboard** — search, CRUD, graph view, export/import (Flask, localhost by default)
- **MCP server** — stdio for local agents; HTTP over Tailscale for remote agents (fail-closed auth)
- **24/7 sync daemon** — incremental sync (file mtime, Gmail pagination, Calendar `syncToken` with 410 recovery)
- **Obsidian-compatible export** — one markdown file per item with YAML frontmatter and `[[backlinks]]`
- **Encrypted at rest** — SQLCipher per-page encryption, including the WAL. No plaintext temp files.

## Structure

```
personal-brain/
├── src/
│   ├── brain_db.py              # SQLCipher database layer + all operations
│   ├── brain_connectors.py      # Gmail, Calendar, Twitter/X, markdown notes
│   ├── brain_embeddings.py      # Embeddings (sentence-transformers or TF-IDF fallback)
│   └── brain_integration.py     # Agent read/write flow (pre/post response)
├── templates/                   # Jinja2 templates for the dashboard
├── tests/                       # Smoke tests + concurrency regression tests
├── Notes/                       # Your markdown notes (configure BRAIN_NOTES_FOLDER)
├── brain.db.enc                  # Encrypted database (created on first run)
├── brain_web_dashboard.py       # Flask web GUI (default http://127.0.0.1:5000)
├── brain_mcp_server.py          # MCP server (stdio / HTTP via Tailscale)
├── brain_sync_daemon.py         # Background sync (notes + Gmail + Calendar)
├── brain_voice.py               # Caller detection + contact context for voice
├── manage_brain.py              # Interactive local CLI (no Google/Twitter needed)
├── requirements.txt
└── .env.example
```

## Install

Python 3.11+ recommended.

```bash
pip install -r requirements.txt
# Optional, for real semantic embeddings:
pip install -r requirements-embeddings.txt
```

`sqlcipher3-binary` ships prebuilt wheels for Windows, macOS, and Linux on common
Python versions. If no wheel exists for your platform, build `sqlcipher3` against
libsqlcipher, or run without `BRAIN_DB_KEY` (unencrypted) on an encrypted disk.

## Configuration

Copy `.env.example` to `.env` and load it into your environment before running any
component (e.g. with your shell, direnv, or a process manager).

| Variable | Required | Purpose |
|---|---|---|
| `BRAIN_DB_KEY` | **Required** | base64 of 32 bytes. Encrypts `brain.db` with SQLCipher (AES-256 per-page). **Fail-closed**: `get_connection()` raises `ValueError` if unset or empty — there is no plaintext fallback. |
| `BRAIN_DASHBOARD_USER` / `BRAIN_DASHBOARD_PASSWORD` | **Required** | Dashboard login. Both must be set; auth is enforced on every request (fail-closed). |
| `BRAIN_DASHBOARD_SECRET` | Optional | Flask session secret. Auto-generated via `secrets.token_hex(32)` if unset (sessions reset on restart). |
| `BRAIN_API_KEY` | **Required** | Protects web API + `/export` and `/import` endpoints (`X-API-Key` header). Fail-closed — missing key returns 401. |
| `BRAIN_MCP_API_KEY_READ` / `BRAIN_MCP_API_KEY_WRITE` | **Required for HTTP MCP** | Scope-based MCP access keys. Read-only tools require READ; read-write tools require WRITE (implies READ). Legacy `BRAIN_MCP_API_KEY` acts as both. HTTP server refuses to start without at least one key configured. |
| `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` | For Gmail/Calendar | Google Cloud OAuth client (optional — only needed for Opsi B sync) |
| `GOOGLE_TOKEN_FILE` | For daemon | OAuth token file (default `google_token.json`) |
| `TWITTER_BEARER_TOKEN` | For Twitter/X | API v2 bearer token (optional) |
| `BRAIN_NOTES_FOLDER` | Optional | Markdown notes folder (default `./Notes`) |
| `BRAIN_DAEMON_INTERVAL` | Optional | Sync interval in seconds (default 300) |
| `BRAIN_EMBEDDING_MODEL` | Optional | HuggingFace model id (default `sentence-transformers/all-MiniLM-L6-v2`) |

Generate an encryption key:

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

> **Keep this key safe.** Losing it means losing the database. A wrong key fails
> fast at startup instead of silently creating an empty brain.

## Quick start

**Option 1 — local only (no Google/Twitter):**

```bash
python manage_brain.py        # interactive menu: contacts, notes sync, search
```

**Option 2 — web dashboard:**

```bash
python brain_web_dashboard.py --port 5000
# open http://127.0.0.1:5000
```

**Option 3 — background sync daemon:**

```bash
python brain_sync_daemon.py --once                 # single sync, then exit
python brain_sync_daemon.py --interval 300         # run forever
python brain_sync_daemon.py --no-gmail --no-calendar   # notes only
```

## Running the components

| Component | Command |
|---|---|
| Web dashboard | `python brain_web_dashboard.py --port 5000` |
| MCP server (local, stdio) | `python brain_mcp_server.py --stdio` |
| MCP server (HTTP, Tailscale) | `python brain_mcp_server.py --tailscale --port 8765` |
| Voice integration | `python brain_voice.py --simulate-call "+123****7890"` |
| Sync daemon | `python brain_sync_daemon.py --interval 300` |

All components can run **at the same time** — SQLCipher + WAL handles concurrent
access safely (this was not true for the old Fernet architecture).

## Remote MCP via Tailscale

1. Install and log in to [Tailscale](https://tailscale.com/download) on both machines: `tailscale up`
2. On the brain machine: `python brain_mcp_server.py --tailscale --port 8765`
3. From the other machine, connect to `http://<tailscale-ip>:8765/mcp`
4. Set `BRAIN_MCP_API_KEY` and pass it as the `api_key` parameter in MCP tool calls.

The HTTP server **refuses to start** without `BRAIN_MCP_API_KEY`. Never expose
port 8765 to the public internet.

## Obsidian export / import (backlinks)

Export every contact, note, knowledge entry, and entity as its own markdown file
with YAML frontmatter and wiki-style `[[Name]]` links:

```bash
python -c "import sys; sys.path.insert(0, 'src'); from brain_db import get_connection, export_brain_markdown_with_backlinks; conn = get_connection(); print(export_brain_markdown_with_backlinks(conn, 'brain_export')); conn.close()"
```

Output: `brain_export/{index.md, graph.md, contacts/, knowledge/, notes/, entities/}`.
Import back with `import_brain_markdown_with_backlinks(conn, 'brain_export')`.

## Migrating from the old Fernet format (`brain.db.enc`)

If you used a version before SQLCipher:

```bash
pip install sqlcipher3-binary cryptography
BRAIN_DB_KEY=<your-existing-key> python migrate_to_sqlcipher.py
```

The script reuses your existing key, verifies the result, and keeps the old file
as `brain.db.enc.bak` until you are confident. Delete the backup manually later.

## Database tables

| Table | Contents |
|---|---|
| `contacts` | People (name, email, phone, company, notes) |
| `emails` | Gmail messages |
| `calendar_events` | Google Calendar events |
| `tweets` | Twitter/X posts |
| `notes` | Markdown notes |
| `knowledge_entries` | Ideas, findings, connections |
| `conversations` | Agent conversation history |
| `entities` / `entity_relations` | Knowledge graph nodes / edges |
| `embeddings` | Vectors for semantic search |
| `provenance_entries` | Source trail for every fact |
| `users` / `item_shares` | Access control (experimental) |

## Security model

- **At rest**: SQLCipher AES-256 per-page encryption, WAL included. Plaintext never touches disk.
- **Dashboard**: required login (werkzeug-hashed password), CSRF protection, login rate limiting (5/min), secure session cookies, constant-time API key comparison. `/export` and `/import` require login **and** API key.
- **MCP**: fail-closed header-based auth (scope keys `BRAIN_MCP_API_KEY_READ` / `BRAIN_MCP_API_KEY_WRITE`), `hmac.compare_digest` timing-safe comparison; remote access only over your private Tailscale network.

**Known limitations (contributions welcome):**
- Multi-user access control (`users` / `item_shares` tables) is experimental — single-user by default.
- Without `BRAIN_DB_KEY`, the application refuses to start — use full-disk encryption if you cannot set the env var.

## Development & tests

```bash
pip install -r requirements-dev.txt
python -m pytest tests/ -v
```

CI runs the suite on Python 3.11 and 3.12 for every push/PR, including a
regression test proving two concurrent connections never lose writes.

## Roadmap

- [ ] Remove legacy HTML string constants from the dashboard
- [ ] Multi-user access control (`users`/`item_shares` — experimental, single-user by default)

## License

MIT — see [LICENSE](LICENSE).
