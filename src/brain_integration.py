"""Brain Integration - Connects brain database to agent workflow.

Before the agent answers: query brain for relevant context.
After the conversation: write new information back to brain.
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
)


def get_brain_context(query, limit_per_source=5):
    """Retrieve relevant context from brain before the agent answers.

    Returns:
        dict with structured context: contacts, emails, calendar, notes, knowledge.
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
        return {
            "query": query,
            "contacts": contact_details,
            "emails": emails,
            "events": events,
            "notes": notes,
            "knowledge": knowledge,
            "has_context": bool(contacts or emails or events or notes or knowledge),
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
    lines.append("=== END BRAIN CONTEXT ===\n")
    return "\n".join(lines)


def extract_entities_from_message(message):
    """Extract entities (person names, emails, topics) from user message."""
    entities = {"people": [], "emails": [], "topics": []}
    email_matches = re.findall(r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}', message)
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
    for person in entities["people"]:
        existing = [dict(r) for r in search_contacts(conn, person, limit=1)]
        if not existing:
            upsert_contact(
                conn,
                name=person,
                source="conversation",
                context_notes=f"Found in conversation: {message[:100]}"
            )
    if entities["people"] or entities["emails"]:
        entry_title = f"Conversation: {message[:50]}"
        entry_content = f"User: {message}\nAgent: {agent_response}"
        related_ids = [e['id'] for e in context.get("contacts", [])]
        insert_knowledge_entry(
            conn,
            entry_type="conversation",
            title=entry_title,
            content=entry_content,
            related_contact_ids=related_ids if related_ids else None,
            source="chat"
        )
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
    """Full flow: read brain → prepare context → agent answers → write to brain."""
    brain_context = get_brain_context(user_message)
    prompt = prepare_agent_prompt(user_message, brain_context)
    agent_response = agent_respond_fn(prompt)
    conn = get_connection()
    try:
        save_conversation_to_brain(conn, user_message, agent_response,
                                   context_used=[c['id'] for c in brain_context.get("contacts", [])])
        extract_and_save_knowledge(conn, user_message, agent_response, brain_context)
    finally:
        conn.close()
    return agent_response
