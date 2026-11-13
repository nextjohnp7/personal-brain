"""Brain MCP Server - Exposes Personal Brain as an MCP server.

Allows other agents (Hermes, Claude Code, Cursor, etc.) to access
your personal brain remotely via the Model Context Protocol.

Run:
    python brain_mcp_server.py --stdio      # stdio transport (local)
    python brain_mcp_server.py --http --port 8765  # HTTP transport (remote)
"""

import sys
import os
import subprocess
import argparse
import logging
import hmac
import contextvars
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger("brain-mcp-server")

# Add src to path
sys.path.insert(0, str(Path(__file__).parent / "src"))

try:
    from mcp.server.fastmcp import FastMCP
    from mcp.types import TextContent
except ImportError:
    logger.error("MCP package not installed. Run: pip install mcp")
    sys.exit(1)

from starlette.middleware.base import BaseHTTPMiddleware
import uvicorn

from brain_db import (
    get_connection,
    search_contacts,
    search_emails,
    get_upcoming_events,
    search_notes,
    search_knowledge,
    semantic_search,
    query_knowledge_graph,
    upsert_contact,
    upsert_entity,
    add_entity_relation,
    log_conversation,
)

mcp = FastMCP("Personal Brain")

# MCP API key protection
BRAIN_MCP_API_KEY = os.environ.get("BRAIN_MCP_API_KEY", "")
BRAIN_MCP_API_KEY_READ = os.environ.get("BRAIN_MCP_API_KEY_READ", "")
BRAIN_MCP_API_KEY_WRITE = os.environ.get("BRAIN_MCP_API_KEY_WRITE", "")

# Resolve effective keys: WRITE implies READ; legacy BRAIN_MCP_API_KEY = both
EFFECTIVE_READ_KEY = BRAIN_MCP_API_KEY_READ or BRAIN_MCP_API_KEY or ""
EFFECTIVE_WRITE_KEY = BRAIN_MCP_API_KEY_WRITE or BRAIN_MCP_API_KEY or ""

# Context var to carry API key from HTTP header into tool functions
_mcp_api_key_ctx = contextvars.ContextVar("mcp_api_key", default="")


class MCPAuthMiddleware(BaseHTTPMiddleware):
    """Extract API key from Authorization: Bearer or X-API-Key header."""

    async def dispatch(self, request, call_next):
        auth_header = request.headers.get("authorization", "")
        x_api_key = request.headers.get("x-api-key", "")
        key = ""
        if auth_header.startswith("Bearer "):
            key = auth_header[len("Bearer "):]
        elif x_api_key:
            key = x_api_key
        _mcp_api_key_ctx.set(key)
        try:
            return await call_next(request)
        finally:
            _mcp_api_key_ctx.set("")


def _get_effective_key(api_key_param: str) -> str:
    """Return API key from parameter (stdio) or header context (HTTP)."""
    if api_key_param:
        return api_key_param
    return _mcp_api_key_ctx.get()


def _check_mcp_api_key(api_key: str, scope: str = "read") -> str | None:
    """Returns error message if API key invalid, None if valid.

    Fail-closed: if no MCP API key is configured at all, all requests are denied.
    If auth is configured but no key is provided, requests are also denied.

    Args:
        api_key: API key from parameter or header context
        scope: "read" for read-only tools, "write" for read-write tools
    """
    effective_key = _get_effective_key(api_key)

    # Fail-closed: no keys configured at all → deny everything
    if not EFFECTIVE_READ_KEY and not EFFECTIVE_WRITE_KEY:
        return (
            "MCP authentication not configured. "
            "Set BRAIN_MCP_API_KEY_READ or BRAIN_MCP_API_KEY_WRITE."
        )

    # Missing key when auth is configured → deny
    if not effective_key:
        return (
            "Missing MCP API key. "
            "Provide api_key parameter or Authorization: Bearer header."
        )

    read_key = EFFECTIVE_READ_KEY
    write_key = EFFECTIVE_WRITE_KEY

    if scope == "write":
        # Write key grants both read and write
        if not write_key or not hmac.compare_digest(effective_key, write_key):
            return "Invalid or missing MCP API key (write scope required). Set BRAIN_MCP_API_KEY_WRITE."
    else:
        # Read scope: accept read key or write key
        read_ok = read_key and hmac.compare_digest(effective_key, read_key)
        write_ok = write_key and hmac.compare_digest(effective_key, write_key)
        if not read_ok and not write_ok:
            return "Invalid or missing MCP API key (read scope required). Set BRAIN_MCP_API_KEY_READ."
    return None


def _conn():
    return get_connection()


@mcp.tool()
def brain_search(query: str, limit: int = 10, offset: int = 0, api_key: str = "") -> str:
    """Search the personal brain across contacts, emails, calendar, notes, knowledge, and semantic matches.

    Results are ranked by relevance across all sources (title/name match
    scores higher than body/content match).

    Args:
        query: Search keyword or phrase
        limit: Maximum total results (default: 10)
        offset: Results offset for pagination (default: 0)
        api_key: MCP API key (stdio only; for HTTP, use Authorization: Bearer ***

    Auth:
        Requires BRAIN_MCP_API_KEY_READ or BRAIN_MCP_API_KEY_WRITE.
        On HTTP, pass via Authorization: Bearer *** or X-API-Key: *** header.
    """
    auth_error = _check_mcp_api_key(api_key, scope="read")
    if auth_error:
        return auth_error
    conn = _conn()
    try:
        contacts = search_contacts(conn, query, limit=limit * 2, offset=0)
        emails = search_emails(conn, query, limit=limit * 2, offset=0)
        events = get_upcoming_events(conn, limit=limit * 2, offset=0, query=query)
        notes = search_notes(conn, query, limit=limit * 2, offset=0)
        knowledge = search_knowledge(conn, query, limit=limit * 2, offset=0)
        entities = search_entities(conn, query, limit=limit * 2, offset=0)
        semantic = semantic_search(conn, query, limit=limit * 2)

        # Build a unified, relevance-ranked result list across all sources.
        # Source weights: contacts and knowledge are most relevant to a person,
        # semantic matches are weighted lower (meaning-based, not exact).
        SOURCE_WEIGHT = {
            'contact': 1.0,
            'knowledge': 0.95,
            'entity': 0.9,
            'note': 0.85,
            'email': 0.8,
            'calendar': 0.75,
            'semantic': 0.6,
        }

        ranked = []
        seen = set()  # deduplicate by (source, id)

        for c in contacts:
            key = ('contact', c.get('id'))
            if key in seen:
                continue
            seen.add(key)
            ranked.append({
                'source': 'contact',
                'id': c.get('id'),
                'relevance': c.get('_relevance', 0.0) * SOURCE_WEIGHT['contact'],
                'title': c.get('name', '(no name)'),
                'subtitle': c.get('email', 'no email'),
                'detail': f"Company: {c.get('company', '-')} | Role: {c.get('role', '-')}",
            })
        for e in emails:
            key = ('email', e.get('message_id') or e.get('id'))
            if key in seen:
                continue
            seen.add(key)
            ranked.append({
                'source': 'email',
                'id': e.get('id'),
                'relevance': e.get('_relevance', 0.0) * SOURCE_WEIGHT['email'],
                'title': e.get('subject', '(no subject)'),
                'subtitle': f"{e.get('sender', '')} → {e.get('recipient', '')}",
                'detail': e.get('received_at', ''),
            })
        for ev in events:
            key = ('calendar', ev.get('event_id') or ev.get('id'))
            if key in seen:
                continue
            seen.add(key)
            ranked.append({
                'source': 'calendar',
                'id': ev.get('id'),
                'relevance': ev.get('_relevance', 0.0) * SOURCE_WEIGHT['calendar'],
                'title': ev.get('title', '(no title)'),
                'subtitle': ev.get('start_time', ''),
                'detail': ev.get('location', ''),
            })
        for n in notes:
            key = ('note', n.get('id'))
            if key in seen:
                continue
            seen.add(key)
            ranked.append({
                'source': 'note',
                'id': n.get('id'),
                'relevance': n.get('_relevance', 0.0) * SOURCE_WEIGHT['note'],
                'title': n.get('title', '(no title)'),
                'subtitle': n.get('filename', ''),
                'detail': (n.get('content', '') or '')[:120],
            })
        for k in knowledge:
            key = ('knowledge', k.get('id'))
            if key in seen:
                continue
            seen.add(key)
            ranked.append({
                'source': 'knowledge',
                'id': k.get('id'),
                'relevance': k.get('_relevance', 0.0) * SOURCE_WEIGHT['knowledge'],
                'title': k.get('title', '(no title)'),
                'subtitle': k.get('entry_type', ''),
                'detail': (k.get('content', '') or '')[:120],
            })
        for ent in entities:
            key = ('entity', ent.get('id'))
            if key in seen:
                continue
            seen.add(key)
            ranked.append({
                'source': 'entity',
                'id': ent.get('id'),
                'relevance': ent.get('_relevance', 0.0) * SOURCE_WEIGHT['entity'],
                'title': ent.get('name', '(no name)'),
                'subtitle': ent.get('entity_type', ''),
                'detail': (ent.get('context_notes', '') or '')[:120],
            })
        for s in semantic:
            key = ('semantic', f"{s.get('source_type')}:{s.get('source_id')}")
            if key in seen:
                continue
            seen.add(key)
            # Normalize semantic similarity (0-1) to same scale as keyword relevance
            ranked.append({
                'source': 'semantic',
                'id': s.get('id'),
                'relevance': s.get('similarity', 0.0) * SOURCE_WEIGHT['semantic'],
                'title': f"[{s.get('source_type', '')}] semantic match",
                'subtitle': s.get('source_id', ''),
                'detail': (s.get('content_chunk', '') or '')[:150],
            })

        # Sort by relevance descending, then apply offset + limit
        ranked.sort(key=lambda x: x['relevance'], reverse=True)
        paginated = ranked[offset:offset + limit]

        lines = [f"# Brain Search Results for: {query}\n"]
        lines.append(f"_Total matches: {len(ranked)} | Showing {offset + 1}-{min(offset + limit, len(ranked))}_\n")

        if not paginated:
            lines.append("_No results found._")
            return "\n".join(lines)

        # Group by source for readable output
        by_source = {}
        for item in paginated:
            by_source.setdefault(item['source'], []).append(item)

        source_labels = {
            'contact': 'Contacts',
            'email': 'Emails',
            'calendar': 'Calendar Events',
            'note': 'Notes',
            'knowledge': 'Knowledge Entries',
            'entity': 'Entities',
            'semantic': 'Semantic Matches',
        }
        for src in ('contact', 'knowledge', 'entity', 'note', 'email', 'calendar', 'semantic'):
            items = by_source.get(src, [])
            if not items:
                continue
            lines.append(f"## {source_labels[src]}")
            for item in items:
                lines.append(
                    f"- {item['title']} (relevance: {item['relevance']:.2f})"
                )
                if item['subtitle']:
                    lines.append(f"  {item['subtitle']}")
                if item['detail']:
                    lines.append(f"  {item['detail']}")

        return "\n".join(lines)
    finally:
        conn.close()


@mcp.tool()
def brain_contact_context(name: str, api_key: str = "") -> str:
    """Get full context for a specific contact including emails, calendar events, and knowledge graph relations.

    Args:
        name: Contact name to look up
        api_key: MCP API key (stdio only; for HTTP, use Authorization: Bearer header)

    Auth:
        Requires BRAIN_MCP_API_KEY_READ or BRAIN_MCP_API_KEY_WRITE.
    """
    auth_error = _check_mcp_api_key(api_key, scope="read")
    if auth_error:
        return auth_error
    conn = _conn()
    try:
        contacts = [dict(r) for r in search_contacts(conn, name, limit=1)]
        if not contacts:
            return f"No contact found matching: {name}"

        c = contacts[0]
        cid = c['id']
        lines = [f"# Contact Context: {c['name']}\n"]
        lines.append(f"- Email: {c.get('email', 'no email')}")
        lines.append(f"- Phone: {c.get('phone', 'no phone')}")
        lines.append(f"- Company: {c.get('company', 'no company')}")
        lines.append(f"- Role: {c.get('role', 'no role')}")
        lines.append(f"- Source: {c.get('source', 'manual')}")
        if c.get('context_notes'):
            lines.append(f"- Notes: {c['context_notes']}")
        if c.get('last_interaction'):
            lines.append(f"- Last interaction: {c['last_interaction']}")

        # Emails
        from brain_db import get_emails_for_contact
        emails = [dict(r) for r in get_emails_for_contact(conn, cid, limit=5)]
        if emails:
            lines.append("\n## Recent Emails")
            for e in emails:
                lines.append(f"- {e.get('sender', '')} → {e.get('subject', '(no subject)')} ({e.get('received_at', '')})")

        # Knowledge graph relations
        from brain_db import upsert_entity, get_entity_relations, search_entities
        entities = search_entities(conn, c['name'], limit=1)
        if entities:
            eid = entities[0]['id']
            rels = get_entity_relations(conn, eid, limit=10)
            if rels:
                lines.append("\n## Knowledge Graph Relations")
                for r in rels:
                    lines.append(f"- {r['source_name']} →{r['relation_type']}→ {r['target_name']}")

        return "\n".join(lines)
    finally:
        conn.close()


@mcp.tool()
def brain_add_contact(name: str, email: str = "", phone: str = "", company: str = "", role: str = "", notes: str = "", api_key: str = "") -> str:
    """Add or update a contact in the personal brain.

    Args:
        name: Contact name
        email: Email address (optional)
        phone: Phone number (optional)
        company: Company name (optional)
        role: Job role (optional)
        notes: Free-form notes (optional)
        api_key: MCP API key (stdio only; for HTTP, use Authorization: Bearer header)

    Auth:
        Requires BRAIN_MCP_API_KEY_WRITE (grants both read and write).
    """
    auth_error = _check_mcp_api_key(api_key, scope="write")
    if auth_error:
        return auth_error
    if os.environ.get("BRAIN_MCP_READ_ONLY", "").lower() in ("1", "true", "yes"):
        return "MCP server is in read-only mode. Write operations are disabled."
    conn = _conn()
    try:
        cid = upsert_contact(
            conn,
            name=name,
            email=email or None,
            phone=phone or None,
            company=company or None,
            role=role or None,
            source="mcp",
            context_notes=notes or None,
        )
        return f"Contact saved with ID: {cid}"
    finally:
        conn.close()


@mcp.tool()
def brain_add_relation(source_name: str, target_name: str, relation_type: str, source: str = "manual", confidence: float = 1.0, api_key: str = "") -> str:
    """Add a relation between two entities in the knowledge graph.

    Args:
        source_name: Source entity name
        target_name: Target entity name
        relation_type: Type of relation (e.g., 'works_at', 'knows', 'met_with')
        source: Source of the relation (default: manual)
        confidence: Confidence score 0.0-1.0 (default: 1.0)
        api_key: MCP API key (stdio only; for HTTP, use Authorization: Bearer header)

    Auth:
        Requires BRAIN_MCP_API_KEY_WRITE (grants both read and write).
    """
    auth_error = _check_mcp_api_key(api_key, scope="write")
    if auth_error:
        return auth_error
    if os.environ.get("BRAIN_MCP_READ_ONLY", "").lower() in ("1", "true", "yes"):
        return "MCP server is in read-only mode. Write operations are disabled."
    conn = _conn()
    try:
        src_eid = upsert_entity(conn, name=source_name, entity_type='person', source='mcp')
        tgt_eid = upsert_entity(conn, name=target_name, entity_type='person', source='mcp')
        rid = add_entity_relation(
            conn,
            source_entity_id=src_eid,
            target_entity_id=tgt_eid,
            relation_type=relation_type,
            source=source,
            confidence=confidence,
        )
        return f"Relation added (ID: {rid}): {source_name} →{relation_type}→ {target_name}"
    finally:
        conn.close()


@mcp.tool()
def brain_semantic_search(query: str, limit: int = 10, api_key: str = "") -> str:
    """Semantic search using TF-IDF + cosine similarity. Finds content by meaning, not just keywords.

    Args:
        query: Search phrase
        limit: Maximum results (default: 10)
        api_key: MCP API key (stdio only; for HTTP, use Authorization: Bearer header)

    Auth:
        Requires BRAIN_MCP_API_KEY_READ or BRAIN_MCP_API_KEY_WRITE.
    """
    auth_error = _check_mcp_api_key(api_key, scope="read")
    if auth_error:
        return auth_error
    conn = _conn()
    try:
        results = semantic_search(conn, query, limit=limit)
        if not results:
            return f"No semantic matches for: {query}"

        lines = [f"# Semantic Search: {query}\n"]
        for s in results:
            lines.append(f"- [{s['source_type']}] {s['content_chunk'][:200]} (relevance: {s['similarity']})")
        return "\n".join(lines)
    finally:
        conn.close()


@mcp.tool()
def brain_knowledge_graph(name: str, limit: int = 10, api_key: str = "") -> str:
    """Query the knowledge graph for a person or entity, showing all relations.

    Args:
        name: Entity name to query
        limit: Maximum relations (default: 10)
        api_key: MCP API key (stdio only; for HTTP, use Authorization: Bearer header)

    Auth:
        Requires BRAIN_MCP_API_KEY_READ or BRAIN_MCP_API_KEY_WRITE.
    """
    auth_error = _check_mcp_api_key(api_key, scope="read")
    if auth_error:
        return auth_error
    conn = _conn()
    try:
        graph = query_knowledge_graph(conn, name, limit=limit)
        lines = [f"# Knowledge Graph: {name}\n"]

        if graph["entities"]:
            lines.append("## Entities")
            for e in graph["entities"]:
                lines.append(f"- {e['name']} ({e.get('entity_type', 'unknown')})")

        if graph["relations"]:
            lines.append("\n## Relations")
            for r in graph["relations"]:
                lines.append(f"- {r['source_name']} →{r['relation_type']}→ {r['target_name']} (confidence: {r['confidence']})")

        if not graph["entities"] and not graph["relations"]:
            lines.append("_No graph data found._")

        return "\n".join(lines)
    finally:
        conn.close()


def get_tailscale_ip():
    """Detect Tailscale IP address (100.x.y.z) if Tailscale is running.

    Returns the first Tailscale IP found, or None.
    """
    try:
        result = subprocess.run(
            ["tailscale", "ip", "-4"],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0:
            ip = result.stdout.strip()
            if ip:
                return ip
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        pass
    return None


def main():
    parser = argparse.ArgumentParser(description="Brain MCP Server")
    parser.add_argument(
        "--stdio",
        action="store_true",
        help="Use stdio transport (for local agent integration)",
    )
    parser.add_argument(
        "--http",
        action="store_true",
        help="Use HTTP transport (for remote access via Tailscale)",
    )
    parser.add_argument(
        "--host",
        type=str,
        default="0.0.0.0",
        help="HTTP bind host (default: 0.0.0.0 for Tailscale remote access)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8765,
        help="HTTP port (default: 8765)",
    )
    parser.add_argument(
        "--tailscale",
        action="store_true",
        help="Auto-detect Tailscale IP and bind to it (implies --http)",
    )
    args = parser.parse_args()

    # Determine bind host
    host = args.host
    if args.tailscale:
        ts_ip = get_tailscale_ip()
        if ts_ip:
            host = ts_ip
            logger.info(f"Tailscale IP detected: {ts_ip}")
        else:
            logger.warning(
                "Tailscale not found or not running. Falling back to 0.0.0.0"
            )
            host = "0.0.0.0"
        args.http = True  # tailscale implies HTTP

    if args.http:
        if not EFFECTIVE_READ_KEY and not EFFECTIVE_WRITE_KEY:
            logger.error(
                "BRAIN_MCP_API_KEY_READ or BRAIN_MCP_API_KEY_WRITE not set — "
                "refusing to start MCP HTTP server without auth. "
                "Set BRAIN_MCP_API_KEY_READ (or BRAIN_MCP_API_KEY_WRITE) environment "
                "variable or use --stdio for local access."
            )
            sys.exit(1)
        # Get ASGI app and wrap with auth middleware (header-based API key)
        starlette_app = mcp.streamable_http_app()
        app = MCPAuthMiddleware(starlette_app)
        logger.info(f"Starting Brain MCP Server on HTTP {host}:{args.port}")
        logger.info(f"Tailscale clients can connect at: {host}:{args.port}")
        logger.info("Auth: pass Authorization: Bearer <key> or X-API-Key: <key> header")
        uvicorn.run(app, host=host, port=args.port, log_level="info")
    else:
        # Default to stdio for local agent integration
        logger.info("Starting Brain MCP Server on stdio")
        mcp.run(transport="stdio")


if __name__ == "__main__":
    main()