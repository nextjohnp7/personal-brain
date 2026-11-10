"""Brain Integration - Connects brain database to agent workflow.

Before the agent answers: query brain for relevant context (keyword + semantic + knowledge graph).
After the conversation: write new information back to brain (entities, relations, embeddings).
"""

import json
import re
from datetime import datetime
from pathlib import Path

from brain_db import (
    get_connection,
    search_contacts,
    search_emails,
    get_emails_for_contact,
    get_upcoming_events,
    search_notes,
    search_knowledge,
    global_search,
    insert_knowledge_entry,
    log_conversation,
    upsert_contact,
    update_contact_interaction,
    store_embedding,
    semantic_search,
    upsert_entity,
    add_entity_relation,
    get_entity_relations,
    find_entity_path,
    query_knowledge_graph,
    search_entities,
)


def get_brain_context(query, limit_per_source=5):
    """Retrieve relevant context from brain before the agent answers.

    Combines keyword search, semantic search (TF-IDF), and knowledge graph.

    Returns:
        dict with structured context: contacts, emails, calendar, notes, knowledge,
        semantic_results, graph_entities, graph_relations.
    """
    conn = get_connection()
    try:
        contacts = [dict(r) for r in search_contacts(conn, query, limit_per_source)]
        contact_details = []
        for c in contacts:
            cid = c['id']
            emails = [dict(r) for r in get_emails_for_contact(conn, cid, limit=3)]
            contact_details.append({
                **dict(c),
                "recent_emails": emails,
            })
        emails = [dict(r) for r in search_emails(conn, query, limit=limit_per_source)]
        events = [dict(r) for r in get_upcoming_events(conn, limit=limit_per_source)]
        notes = [dict(r) for r in search_notes(conn, query, limit=limit_per_source)]
        knowledge = [dict(r) for r in search_knowledge(conn, query, limit=limit_per_source)]
        semantic_results = semantic_search(conn, query, limit=limit_per_source)
        graph = query_knowledge_graph(conn, query, limit=limit_per_source)

        has_context = bool(
            contacts or emails or events or notes or knowledge
            or semantic_results or graph["entities"] or graph["relations"]
        )

        return {
            "query": query,
            "contacts": contact_details,
            "emails": emails,
            "events": events,
            "notes": notes,
            "knowledge": knowledge,
            "semantic_results": semantic_results,
            "graph_entities": graph["entities"],
            "graph_relations": graph["relations"],
            "has_context": has_context,
        }
    finally:
        conn.close()


def format_brain_context(context):
    """Format brain context into text readable by the agent/LLM."""
    if not context or not context.get("has_context"):
        return ""
    lines = ["\n=== BRAIN CONTEXT (your personal data) ==="]
    if context["contacts"]:
        lines.append("\n-- Relevant contacts --")
        for c in context["contacts"]:
            lines.append(f"• {c['name']} ({c.get('email', 'no email')})")
            if c.get('company'):
                lines.append(f"  Company: {c['company']}")
            if c.get('role'):
                lines.append(f"  Role: {c['role']}")
            if c.get('last_interaction'):
                lines.append(f"  Last interaction: {c['last_interaction']}")
            if c.get('context_notes'):
                lines.append(f"  Notes: {c['context_notes']}")
            if c.get('recent_emails'):
                lines.append("  Recent emails:")
                for e in c['recent_emails'][:2]:
                    lines.append(f"    - {e.get('subject', '(no subject)')} ({e.get('received_at', '')})")
    if context["emails"]:
        lines.append("\n-- Relevant emails --")
        for e in context["emails"][:5]:
            lines.append(f"• {e.get('sender', '')} → {e.get('subject', '(no subject)')}")
            lines.append(f"  {e.get('snippet', '')[:150]}")
    if context["events"]:
        lines.append("\n-- Upcoming calendar --")
        for ev in context["events"][:5]:
            lines.append(f"• {ev.get('title', '(no title)')} — {ev.get('start_time', '')}")
    if context["notes"]:
        lines.append("\n-- Personal notes --")
        for n in context["notes"][:5]:
            lines.append(f"• {n.get('title', '(no title)')}")
            if n.get('summary'):
                lines.append(f"  {n['summary'][:200]}")
    if context["knowledge"]:
        lines.append("\n-- Stored knowledge --")
        for k in context["knowledge"][:5]:
            lines.append(f"• [{k.get('entry_type', '')}] {k.get('title', '')}")
            lines.append(f"  {k.get('content', '')[:200]}")
    if context["semantic_results"]:
        lines.append("\n-- Semantic matches (related by meaning) --")
        for s in context["semantic_results"][:5]:
            lines.append(f"• [{s['source_type']}] {s['content_chunk'][:150]} (relevance: {s['similarity']})")
    if context["graph_entities"]:
        lines.append("\n-- Knowledge graph entities --")
        for e in context["graph_entities"][:5]:
            lines.append(f"• {e['name']} ({e.get('entity_type', 'unknown')})")
    if context["graph_relations"]:
        lines.append("\n-- Knowledge graph relations --")
        for r in context["graph_relations"][:5]:
            lines.append(f"• {r['source_name']} →{r['relation_type']}→ {r['target_name']}")
    lines.append("=== END BRAIN CONTEXT ===\n")
    return "\n".join(lines)


def synthesize_answer(user_message, brain_context, agent_response):
    """Synthesize a structured answer with citations from brain context."""
    if not brain_context or not brain_context.get("has_context"):
        return {"answer": agent_response, "sources": []}
    sources = []
    answer_parts = [agent_response]
    if brain_context["contacts"]:
        answer_parts.append("\nRelevant people from your brain:")
        for c in brain_context["contacts"][:5]:
            fact = f"- {c['name']}"
            if c.get('company'):
                fact += f" — {c['company']}"
            if c.get('role'):
                fact += f" ({c['role']})"
            answer_parts.append(fact)
            sources.append({
                "type": "contact",
                "name": c['name'],
                "detail": f"{c.get('email', '')} | {c.get('company', '')} | {c.get('role', '')}",
            })
    if brain_context["emails"]:
        answer_parts.append("\nRelevant emails:")
        for e in brain_context["emails"][:3]:
            answer_parts.append(
                f"- {e.get('sender', '')} -> {e.get('subject', '(no subject)')}"
            )
            sources.append({
                "type": "email",
                "subject": e.get('subject', '(no subject)'),
                "sender": e.get('sender', ''),
            })
    if brain_context["events"]:
        answer_parts.append("\nUpcoming calendar events:")
        for ev in brain_context["events"][:3]:
            answer_parts.append(
                f"- {ev.get('title', '(no title)')} on {ev.get('start_time', '')}"
            )
            sources.append({
                "type": "calendar_event",
                "title": ev.get('title', '(no title)'),
                "time": ev.get('start_time', ''),
            })
    if brain_context["semantic_results"]:
        answer_parts.append("\nRelated content (by meaning):")
        for s in brain_context["semantic_results"][:3]:
            answer_parts.append(
                f"- [{s['source_type']}] {s['content_chunk'][:120]}..."
            )
            sources.append({
                "type": s['source_type'],
                "content": s['content_chunk'][:200],
                "similarity": s['similarity'],
            })
    if brain_context["graph_relations"]:
        answer_parts.append("\nConnections in your knowledge graph:")
        for r in brain_context["graph_relations"][:3]:
            answer_parts.append(
                f"- {r['source_name']} {r['relation_type']} {r['target_name']}"
            )
            sources.append({
                "type": "relation",
                "source": r['source_name'],
                "target": r['target_name'],
                "relation": r['relation_type'],
            })
    if brain_context["notes"]:
        answer_parts.append("\nRelevant notes:")
        for n in brain_context["notes"][:3]:
            answer_parts.append(
                f"- {n.get('title', '(no title)')}: {n.get('summary', '')[:100]}"
            )
            sources.append({
                "type": "note",
                "title": n.get('title', '(no title)'),
                "filename": n.get('filename', ''),
            })
    return {"answer": "\n".join(answer_parts), "sources": sources}


def extract_entities_from_message(message):
    """Extract entities (person names, emails, topics) from user message."""
    entities = {"people": [], "emails": [], "topics": []}
    email_matches = re.findall(r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z](2,)', message)
    entities["emails"] = list(set(email_matches))
    name_patterns = re.findall(
        r'(?:with|from|for|to)\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)',
        message
    )
    entities["people"] = list(set(name_patterns))
    return entities


def save_conversation_to_brain(conn, user_message, agent_response, context_used=None):
    """Save conversation to brain for future learning."""
    return log_conversation(conn, user_message, agent_response, context_used)


def extract_and_save_knowledge(conn, message, agent_response, context):
    """Extract new knowledge from conversation and save to brain."""
    entities = extract_entities_from_message(message)
    contact_id_map = {}
    for person in entities["people"]:
        existing = [dict(r) for r in search_contacts(conn, person, limit=1)]
        if not existing:
            cid = upsert_contact(
                conn,
                name=person,
                source="conversation",
                context_notes=f"Found in conversation: {message[:100]}"
            )
            contact_id_map[person] = cid
        else:
            contact_id_map[person] = existing[0]['id']
    entity_id_map = {}
    for person in entities["people"]:
        eid = upsert_entity(
            conn,
            name=person,
            entity_type='person',
            source='conversation',
            context_notes=f"Mentioned in: {message[:100]}"
        )
        entity_id_map[person] = eid
    work_patterns = re.findall(
        r'([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)\s+(?:works at|is at|joined|at)\s+([A-Z][a-zA-Z]+(?:\s+[A-Z][a-zA-Z]+)*)',
        message
    )
    for person_name, company_name in work_patterns:
        person_eid = upsert_entity(
            conn, name=person_name, entity_type='person', source='conversation'
        )
        company_eid = upsert_entity(
            conn, name=company_name, entity_type='company', source='conversation'
        )
        add_entity_relation(
            conn,
            source_entity_id=person_eid,
            target_entity_id=company_eid,
            relation_type='works_at',
            source='conversation',
            confidence=0.8,
        )
        if person_name in contact_id_map:
            add_entity_relation(
                conn,
                source_entity_id=person_eid,
                target_entity_id=contact_id_map[person_name],
                relation_type='referenced_in_contact',
                source='conversation',
                confidence=1.0,
            )
    if entities["people"] or entities["emails"]:
        entry_title = f"Conversation: {message[:50]}"
        entry_content = f"User: {message}\nAgent: {agent_response}"
        related_ids = [e['id'] for e in context.get("contacts", [])]
        kid = insert_knowledge_entry(
            conn,
            entry_type="conversation",
            title=entry_title,
            content=entry_content,
            related_contact_ids=related_ids if related_ids else None,
            source="chat"
        )
        store_embedding(conn, "knowledge", kid, entry_content)
    conn.commit()


def prepare_agent_prompt(user_message, brain_context):
    """Combine brain context with user message for the agent."""
    context_text = format_brain_context(brain_context)
    if not context_text:
        return user_message
    return f"""{context_text}

User question: {user_message}

Use the context above if relevant to answer. If there is new information
that should be saved to brain, mention it in your response."""


def process_with_brain(user_message, agent_respond_fn):
    """Full flow: read brain → prepare context → agent answers → write to brain.

    Phase 1 enhancements:
    - Semantic search (TF-IDF) included in context
    - Knowledge graph entities and relations included
    - Synthesis layer produces structured answer with citations
    - Entities and relations auto-extracted from conversation
    """
    brain_context = get_brain_context(user_message)
    prompt = prepare_agent_prompt(user_message, brain_context)
    agent_response = agent_respond_fn(prompt)
    synthesis = synthesize_answer(user_message, brain_context, agent_response)
    conn = get_connection()
    try:
        save_conversation_to_brain(conn, user_message, agent_response,
                                   context_used=[c['id'] for c in brain_context.get("contacts", [])])
        extract_and_save_knowledge(conn, user_message, agent_response, brain_context)
    finally:
        conn.close()
    return synthesis
