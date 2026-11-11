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


def _check_mcp_api_key(api_key: str) -> str | None:
    """Returns error message if API key invalid, None if valid (or auth disabled)."""
    if BRAIN_MCP_API_KEY and api_key != BRAIN_MCP_API_KEY:
        return "Invalid or missing MCP API key. Set BRAIN_MCP_API_KEY environment variable."
    return None


def _conn():
    return get_connection()


@mcp.tool()
def brain_search(query: str, limit: int = 10, api_key: str = "") -> str:
    """Search the personal brain across contacts, emails, calendar, notes, knowledge, and semantic matches.

    Args:
        query: Search keyword or phrase
        limit: Maximum results per source (default: 10)
        api_key: MCP API key (required if BRAIN_MCP_API_KEY is set)
    """
    auth_error = _check_mcp_api_key(api_key)
    if auth_error:
        return auth_error
    conn = _conn()
    try:
        contacts = [dict(r) for r in search_contacts(conn, query, limit)]
        emails = [dict(r) for r in search_emails(conn, query, limit)]
        events = [dict(r) for r in get_upcoming_events(conn, limit)]
        notes = [dict(r) for r in search_notes(conn, query, limit)]
        knowledge = [dict(r) for r in search_knowledge(conn, query, limit)]
        semantic = semantic_search(conn, query, limit)
        graph = query_knowledge_graph(conn, query, limit)

        lines = [f"# Brain Search Results for: {query}\n"]

        if contacts:
            lines.append("## Contacts")
            for c in contacts:
                lines.append(f"- {c['name']} ({c.get('email', 'no email')})")
                if c.get('company'):
                    lines.append(f"  Company: {c['company']}")
                if c.get('role'):
                    lines.append(f"  Role: {c['role']}")

        if emails:
            lines.append("\n## Emails")
            for e in emails[:5]:
                lines.append(f"- {e.get('sender', '')} → {e.get('subject', '(no subject)')}")

        if events:
            lines.append("\n## Calendar Events")
            for ev in events[:5]:
                lines.append(f"- {ev.get('title', '(no title)')} — {ev.get('start_time', '')}")

        if notes:
            lines.append("\n## Notes")
            for n in notes[:5]:
                lines.append(f"- {n.get('title', '(no title)')}")

        if knowledge:
            lines.append("\n## Knowledge")
            for k in knowledge[:5]:
                lines.append(f"- [{k.get('entry_type', '')}] {k.get('title', '')}")

        if semantic:
            lines.append("\n## Semantic Matches (by meaning)")
            for s in semantic[:5]:
                lines.append(f"- [{s['source_type']}] {s['content_chunk'][:150]} (relevance: {s['similarity']})")

        if graph["relations"]:
            lines.append("\n## Knowledge Graph Relations")
            for r in graph["relations"][:5]:
                lines.append(f"- {r['source_name']} →{r['relation_type']}→ {r['target_name']}")

        if not any([contacts, emails, events, notes, knowledge, semantic, graph["relations"]]):
            lines.append("_No results found._")

        return "\n".join(lines)
    finally:
        conn.close()


@mcp.tool()
def brain_contact_context(name: str, api_key: str = "") -> str:
    """Get full context for a specific contact including emails, calendar events, and knowledge graph relations.

    Args:
        name: Contact name to look up
        api_key: MCP API key (required if BRAIN_MCP_API_KEY is set)
    """
    auth_error = _check_mcp_api_key(api_key)
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
        api_key: MCP API key (required if BRAIN_MCP_API_KEY is set)
    """
    auth_error = _check_mcp_api_key(api_key)
    if auth_error:
        return auth_error
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
        api_key: MCP API key (required if BRAIN_MCP_API_KEY is set)
    """
    auth_error = _check_mcp_api_key(api_key)
    if auth_error:
        return auth_error
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
        api_key: MCP API key (required if BRAIN_MCP_API_KEY is set)
    """
    auth_error = _check_mcp_api_key(api_key)
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
        api_key: MCP API key (required if BRAIN_MCP_API_KEY is set)
    """
    auth_error = _check_mcp_api_key(api_key)
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
        logger.info(f"Starting Brain MCP Server on HTTP {host}:{args.port}")
        logger.info(f"Tailscale clients can connect at: {host}:{args.port}")
        mcp.run(transport="http", host=host, port=args.port)
    else:
        # Default to stdio for local agent integration
        logger.info("Starting Brain MCP Server on stdio")
        mcp.run(transport="stdio")


if __name__ == "__main__":
    main()