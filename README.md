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
- `BRAIN_MCP_API_KEY` — API key for MCP server access
- `GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET` — from Google Cloud Console (for Gmail + Calendar)
- `TWITTER_BEARER_TOKEN` — from developer.twitter.com (for Twitter/X)
- `BRAIN_NOTES_FOLDER` — path to your markdown notes folder

### 2. Install Python dependencies

```bash
pip install google-auth google-auth-oauthlib google-auth-httplib2 google-api-python-client requests flask cryptography "mcp<2"
```

### 3. Run initial sync

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
| MCP Server | `python brain_mcp_server.py --stdio` or `--http --port 8765` |
| Voice Integration | `python brain_voice.py --simulate-call "+123****7890"` |
| Sync Daemon | `python brain_sync_daemon.py --interval 60` |

## Security

- **Encryption at rest**: `brain.db.enc` is encrypted with Fernet (AES-128-CBC + HMAC). Plaintext never touches disk.
- **Dashboard auth**: Login with `BRAIN_DASHBOARD_USER` / `BRAIN_DASHBOARD_PASSWORD` (hashed with werkzeug).
- **API key**: `BRAIN_API_KEY` for web API, `BRAIN_MCP_API_KEY` for MCP server access.
