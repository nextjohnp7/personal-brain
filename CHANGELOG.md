# Changelog

## [0.1.0] - 2026-11-12
- SQLCipher per-page encryption (AES-256) with WAL mode
- MCP server: stdio + HTTP via Tailscale, with header-based API key + scope-based keys (read-only / read-write)
- Web dashboard: login, CSRF, rate limiting, secure cookies, open-redirect fix, Obsidian-compatible export
- Gmail + Calendar incremental sync (pagination, syncToken with 410 recovery)
- Knowledge graph with provenance tracking
- Semantic search with sentence-transformers (TF-IDF fallback)
- 24/7 sync daemon
- 35 tests, passing on Python 3.11/3.12 across Linux/Windows/macOS
- MIT licensed
