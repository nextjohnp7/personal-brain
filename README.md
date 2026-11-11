# Personal Brain - Knowledge Base for AI Agent

## Structure

```
D:/Project GitHub Brain Memory/
├── src/
│   ├── brain_db.py              # SQLite database schema and operations (encrypted at rest)
│   ├── brain_connectors.py      # Connectors for Gmail, Calendar, Twitter, Notes
│   ├── brain_integration.py     # Brain read/write flow (pre-post response)
│   └── brain_encryption.py      # File-level encryption (Fernet)
├── brain.db.enc                 # Encrypted database (created automatically on first run)
├── Notes/                       # Markdown notes folder (configure BRAIN_NOTES_FOLDER in .env)
├── .env.example                 # Copy to .env and fill in API keys
├── brain_web_dashboard.py       # Flask web GUI (port 5000)
├── brain_mcp_server.py          # MCP server for remote agent access
├── brain_voice.py               # Caller detection + contact context for voice
├── brain_sync_daemon.py         # Background sync daemon for notes folder
└── README.md
```

## Setup

### 1. Copy `.env.example` to `.env` and fill in API keys

```bash
cp .env.example .env
```

Required for encryption:
- `BRAIN_DB_KEY` — base64-encoded 32-byte encryption key (generate with `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`)

Optional but recommended:
- `BRAIN_DASHBOARD_USER` and `BRAIN_DASHBOARD_PASSWORD` — web dashboard login
- `BRAIN_API_KEY` — API key for web API endpoints
- `BRAIN_MCP_API_KEY` — API key for MCP server access (required for remote access)
- `GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET` — from Google Cloud Console (for Gmail + Calendar)
- `TWITTER_BEARER_TOKEN` — from developer.twitter.com (for Twitter/X)
- `BRAIN_NOTES_FOLDER` — path to your markdown notes folder
- `TAILSCALE_ENABLED` — set to `true` if using Tailscale for remote MCP access

### 2. Install Python dependencies

```bash
pip install google-auth google-auth-oauthlib google-auth-httplib2 google-api-python-client requests flask cryptography "mcp<2"
```

### 3. Install Tailscale (for remote MCP access)

Tailscale creates a private VPN so you can access your MCP server from any device without opening ports on your router.

```bash
# Download and install Tailscale: https://tailscale.com/download
# After installation, log in:
tailscale up

# Get your Tailscale IP:
tailscale ip -4
# Example output: 100.x.y.z
```

### 4. Run initial sync

```bash
cd src
python -c "
from brain_connectors import sync_all
# Example: sync notes only
result = sync_all(notes_folder='D:/Project GitHub Brain Memory/Notes')
print(result)
"
```

## Brain Workflow

- **Before the agent answers**: `get_brain_context(query)` searches contacts, emails, calendar, notes, knowledge, semantic matches, and knowledge graph for relevant items, then prepends them to the agent prompt.
- **After the agent answers**: the conversation and new entities (name, email, topic) are saved back to `brain.db` for continuous learning.

## Database Tables (brain.db)

| Table | Contents |
|-------|----------|
| `contacts` | People (name, email, phone, company, notes) |
| `emails` | Emails from Gmail |
| `calendar_events` | Events from Google Calendar |
| `tweets` | Tweets from Twitter/X |
| `notes` | Markdown notes from local folder |
| `knowledge_entries` | New knowledge (connections between people, ideas, findings) |
| `conversations` | Agent conversation history |
| `entities` | Knowledge graph nodes (people, companies, concepts) |
| `entity_relations` | Knowledge graph edges (works_at, knows, met_with, etc.) |
| `embeddings` | TF-IDF vectors for semantic search |

All data is stored **locally** on your computer and encrypted at rest with Fernet. No server is required.

## Running the Components

| Component | Command |
|-----------|---------|
| Web Dashboard | `python brain_web_dashboard.py --port 5000` |
| MCP Server (local) | `python brain_mcp_server.py --stdio` |
| MCP Server (HTTP, Tailscale) | `python brain_mcp_server.py --http --port 8765` |
| MCP Server (auto Tailscale IP) | `python brain_mcp_server.py --tailscale --port 8765` |
| Voice Integration | `python brain_voice.py --simulate-call "+123****7890"` |
| Sync Daemon | `python brain_sync_daemon.py --interval 60` |

### Export Markdown dengan Backlinks

Export brain sebagai file markdown terpisah dengan backlinks wiki-style (`[[Nama]]`), kompatibel dengan Obsidian dan tools knowledge graph lainnya.

```bash
python -c "
from src.brain_db import get_connection, export_brain_markdown_with_backlinks
conn = get_connection()
stats = export_brain_markdown_with_backlinks(conn, 'D:/Project GitHub Brain Memory/brain_export')
print(stats)
"
```

Struktur output:
```
brain_export/
├── index.md              # Indeks semua entitas dengan backlinks
├── graph.md              # Knowledge graph visual (entitas + relasi)
├── contacts/             # Satu file .md per kontak
├── knowledge/            # Satu file .md per entri knowledge
├── notes/                # Satu file .md per markdown note
└── entities/             # Satu file .md per entitas knowledge graph
```

Setiap file memiliki frontmatter YAML (title, type, source, created_at, dll.) dan referensi ke entitas lain secara otomatis diubah menjadi `[[Nama]]` links.

Import kembali:
```bash
python -c "
from src.brain_db import get_connection, import_brain_markdown_with_backlinks
conn = get_connection()
stats = import_brain_markdown_with_backlinks(conn, 'D:/Project GitHub Brain Memory/brain_export')
print(stats)
"
```

### Connecting to MCP remotely via Tailscale

1. Install and log in to Tailscale on both machines
2. On the brain machine, start MCP server:
   ```bash
   python brain_mcp_server.py --tailscale --port 8765
   ```
   Or manually: `python brain_mcp_server.py --http --host 0.0.0.0 --port 8765`
3. From another machine on your Tailscale network, connect to:
   ```
   http://<tailscale-ip>:8765/mcp
   ```
   (Replace `<tailscale-ip>` with your Tailscale IP, e.g., `100.x.y.z`)
4. Set `BRAIN_MCP_API_KEY` in `.env` and pass it as the `api_key` parameter in every MCP tool call.

## Security

- **Encryption at rest**: `brain.db.enc` is encrypted with Fernet (AES-128-CBC + HMAC). Plaintext never touches disk.
- **Dashboard auth**: Login with `BRAIN_DASHBOARD_USER` / `BRAIN_DASHBOARD_PASSWORD` (hashed with werkzeug).
- **API key**: `BRAIN_API_KEY` for web API, `BRAIN_MCP_API_KEY` for MCP server access.
- **Tailscale**: MCP server binds to `0.0.0.0` by default but is only reachable within your private Tailscale network. Never expose port 8765 to the public internet without a firewall.
