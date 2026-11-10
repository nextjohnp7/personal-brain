"""Brain Integration - Menghubungkan brain database dengan alur kerja agen.

Sebelum agen menjawab: query brain untuk konteks relevan.
Setelah percakapan: tulis informasi baru kembali ke brain.
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
    """Ambil konteks relevan dari brain sebelum agen menjawab.

    Returns:
        dict dengan konteks yang terstruktur: kontak, email, kalender, notes, knowledge.
    """
    conn = get_connection()
    try:
        # Cari kontak yang terkait dengan query
        contacts = [dict(r) for r in search_contacts(conn, query, limit_per_source)]

        # Untuk setiap kontak yang ditemukan, ambil riwayat email dan event
        contact_details = []
        for c in contacts:
            cid = c['id']
            emails = [dict(r) for r in get_emails_for_contact(conn, cid, limit=3)]
            contact_details.append({
                **dict(c),
                "recent_emails": emails,
            })

        # Cari email umum
        emails = [dict(r) for r in search_emails(conn, query, limit=limit_per_source)]

        # Event kalender mendatang
        events = [dict(r) for r in get_upcoming_events(conn, limit=limit_per_source)]

        # Catatan markdown
        notes = [dict(r) for r in search_notes(conn, query, limit=limit_per_source)]

        # Entri knowledge
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
    """Format konteks brain menjadi teks yang bisa dibaca agen/LLM."""
    if not context or not context.get("has_context"):
        return ""

    lines = ["\n=== KONTEKS DARI BRAIN (data pribadi Anda) ==="]

    if context["contacts"]:
        lines.append("\n-- Kontak yang relevan --")
        for c in context["contacts"]:
            lines.append(f"• {c['name']} ({c.get('email', 'no email')})")
            if c.get('company'):
                lines.append(f"  Perusahaan: {c['company']}")
            if c.get('role'):
                lines.append(f"  Jabatan: {c['role']}")
            if c.get('last_interaction'):
                lines.append(f"  Interaksi terakhir: {c['last_interaction']}")
            if c.get('context_notes'):
                lines.append(f"  Catatan: {c['context_notes']}")
            if c.get('recent_emails'):
                lines.append("  Email terbaru:")
                for e in c['recent_emails'][:2]:
                    lines.append(f"    - {e.get('subject', '(tanpa subjek)')} ({e.get('received_at', '')})")

    if context["emails"]:
        lines.append("\n-- Email yang relevan --")
        for e in context["emails"][:5]:
            lines.append(f"• {e.get('sender', '')} → {e.get('subject', '(tanpa subjek)')}")
            lines.append(f"  {e.get('snippet', '')[:150]}")

    if context["events"]:
        lines.append("\n-- Kalender mendatang --")
        for ev in context["events"][:5]:
            lines.append(f"• {ev.get('title', '(tanpa judul)')} — {ev.get('start_time', '')}")

    if context["notes"]:
        lines.append("\n-- Catatan pribadi --")
        for n in context["notes"][:5]:
            lines.append(f"• {n.get('title', '(tanpa judul)')}")
            if n.get('summary'):
                lines.append(f"  {n['summary'][:200]}")

    if context["knowledge"]:
        lines.append("\n-- Pengetahuan tersimpan --")
        for k in context["knowledge"][:5]:
            lines.append(f"• [{k.get('entry_type', '')}] {k.get('title', '')}")
            lines.append(f"  {k.get('content', '')[:200]}")

    lines.append("=== AKHIR KONTEKS BRAIN ===\n")
    return "\n".join(lines)


def extract_entities_from_message(message):
    """Ekstrak entitas (nama orang, email, topik) dari pesan user.

    Digunakan untuk mengetahui apa yang harus disimpan ke brain setelah percakapan.
    """
    entities = {
        "people": [],
        "emails": [],
        "topics": [],
    }

    # Cari pola email
    email_matches = re.findall(r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}', message)
    entities["emails"] = list(set(email_matches))

    # Cari pola nama setelah kata kunci seperti "dengan", "dari", "untuk"
    name_patterns = re.findall(
        r'(?:dengan|dari|untuk|kepada)\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)',
        message
    )
    entities["people"] = list(set(name_patterns))

    return entities


def save_conversation_to_brain(conn, user_message, agent_response, context_used=None):
    """Simpan percakapan ke brain untuk pembelajaran di masa depan."""
    return log_conversation(conn, user_message, agent_response, context_used)


def extract_and_save_knowledge(conn, message, agent_response, context):
    """Ekstrak pengetahuan baru dari percakapan dan simpan ke brain.

    Contoh: jika user menyebutkan "Aldi bekerja di Tokopedia",
    simpan sebagai knowledge entry atau update kontak.
    """
    entities = extract_entities_from_message(message)

    # Simpan kontak baru yang ditemukan
    for person in entities["people"]:
        # Cari apakah kontak sudah ada
        existing = [dict(r) for r in search_contacts(conn, person, limit=1)]
        if not existing:
            upsert_contact(
                conn,
                name=person,
                source="conversation",
                context_notes=f"Ditemukan dalam percakapan: {message[:100]}"
            )

    # Simpan knowledge entry jika ada topik penting
    if entities["people"] or entities["emails"]:
        entry_title = f"Percakapan: {message[:50]}"
        entry_content = f"User: {message}\nAgen: {agent_response}"
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
    """Gabungkan konteks brain dengan pesan user untuk agen."""
    context_text = format_brain_context(brain_context)
    if not context_text:
        return user_message

    return f"""{context_text}

Pertanyaan user: {user_message}

Gunakan konteks di atas jika relevan untuk menjawab. Jika ada informasi baru yang 
harus disimpan ke brain, sebutkan dalam respons Anda."""


# =====================================================================
# Contoh penggunaan dalam alur agen
# =====================================================================

def process_with_brain(user_message, agent_respond_fn):
    """Alur lengkap: baca brain → siapkan konteks → agen menjawab → tulis ke brain.

    Args:
        user_message: Pesan dari user
        agent_respond_fn: Fungsi yang menerima (prompt_with_context) dan mengembalikan respons agen

    Returns:
        Respons agen
    """
    # 1. Baca konteks dari brain
    brain_context = get_brain_context(user_message)

    # 2. Siapkan prompt dengan konteks
    prompt = prepare_agent_prompt(user_message, brain_context)

    # 3. Agen menjawab
    agent_response = agent_respond_fn(prompt)

    # 4. Tulis kembali ke brain
    conn = get_connection()
    try:
        save_conversation_to_brain(conn, user_message, agent_response,
                                   context_used=[c['id'] for c in brain_context.get("contacts", [])])
        extract_and_save_knowledge(conn, user_message, agent_response, brain_context)
    finally:
        conn.close()

    return agent_response