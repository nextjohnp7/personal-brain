# Personal Brain - Knowledge Base for AI Agent

## Structure
```
D:/Project GitHub Brain Memory/
├── src/
│   ├── brain_db.py              # SQLite database schema and operations
│   ├── brain_connectors.py      # Connectors for Gmail, Calendar, Twitter, Notes
│   └── brain_integration.py     # Brain read/write flow (pre-post response)
├── brain.db                     # SQLite database (created automatically on first run)
├── Notes/                       # Markdown notes folder (configure BRAIN_NOTES_FOLDER in .env)
├── .env.example                 # Copy to .env and fill in API keys
└── README.md
```

## Setup

1. Copy `.env.example` to `.env` and fill in API keys:
   - `GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET` — from Google Cloud Console
   - `TWITTER_BEARER_TOKEN` — from developer.twitter.com
   - `BRAIN_NOTES_FOLDER` — path to your markdown notes folder

2. Install Python dependencies:
   ```bash
   pip install google-auth google-auth-oauthlib google-auth-httplib2 google-api-python-client requests
   ```

3. Run initial sync:
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

- **Before the agent answers**: `get_brain_context(query)` searches contacts, emails, calendar, notes, and knowledge for relevant items, then prepends them to the agent prompt.
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

All data is stored **locally** on your computer. No server is required.