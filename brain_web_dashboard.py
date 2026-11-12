"""Brain Web Dashboard - Flask web interface for managing the Personal Brain.

Provides a GUI for:
- Searching contacts, notes, knowledge, and semantic matches
- Managing contacts (add, edit, delete)
- Managing notes (add, edit, delete)
- Viewing knowledge graph (entities and relations)
- Exporting/importing brain data (JSON and markdown)
- Viewing conversation history

Run:
    python brain_web_dashboard.py --port 5000
"""

import sys
import os
import json
import argparse
import hmac
import logging
from pathlib import Path
from datetime import datetime, timedelta

sys.path.insert(0, str(Path(__file__).parent / "src"))

from flask import (
    Flask, render_template, request, redirect,
    url_for, jsonify, session, flash,
)
from werkzeug.security import generate_password_hash, check_password_hash
from urllib.parse import urlparse, urljoin
from flask_wtf.csrf import CSRFProtect
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

from brain_db import (
    get_connection,
    DB_PATH,
    search_contacts,
    search_notes,
    search_knowledge,
    global_search,
    semantic_search,
    query_knowledge_graph,
    upsert_contact,
    update_contact,
    delete_contact as delete_contact_db,
    upsert_note,
    update_note,
    delete_note as delete_note_db,
    insert_knowledge_entry,
    update_knowledge_entry,
    delete_knowledge_entry,
    upsert_entity,
    add_entity_relation,
    delete_entity_relation,
    delete_entity,
    export_brain_json,
    export_brain_markdown,
    import_brain_json,
    search_entities,
    upsert_contact_with_owner,
    upsert_note_with_owner,
    get_contacts_by_owner,
    get_notes_by_owner,
    detect_prompt_injection,
    label_item,
    get_contact,
    get_entity_relations,
    count_contacts,
    count_notes,
    count_knowledge,
    count_emails,
    count_events,
    get_db_stats,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger("brain-web-dashboard")

import secrets

app = Flask(__name__)
_secret = os.environ.get("BRAIN_DASHBOARD_SECRET", "")
if not _secret:
    _secret = secrets.token_hex(32)
    logger.warning("BRAIN_DASHBOARD_SECRET not set — using random generated key (sessions invalid on restart)")
app.secret_key = _secret

app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    WTF_CSRF_TIME_LIMIT=3600,
)
csrf = CSRFProtect(app)
limiter = Limiter(key_func=get_remote_address, app=app,
                  default_limits=[], storage_uri="memory://")

def _is_safe_url(target):
    if not target:
        return False
    ref = urlparse(request.host_url)
    test = urlparse(urljoin(request.host_url, target))
    return test.scheme in ("http", "https") and test.netloc == ref.netloc

# Simple API key protection for sharing
BRAIN_API_KEY = os.environ.get("BRAIN_API_KEY", "")

# Web dashboard authentication
DASHBOARD_USER = os.environ.get("BRAIN_DASHBOARD_USER", "")
DASHBOARD_PASSWORD_HASH = os.environ.get("BRAIN_DASHBOARD_PASSWORD_HASH", "")
# If BRAIN_DASHBOARD_PASSWORD is set (plaintext), hash it on first run
DASHBOARD_PASSWORD_PLAIN = os.environ.get("BRAIN_DASHBOARD_PASSWORD", "")
if DASHBOARD_PASSWORD_PLAIN and not DASHBOARD_PASSWORD_HASH:
    DASHBOARD_PASSWORD_HASH = generate_password_hash(DASHBOARD_PASSWORD_PLAIN)

AUTH_ENABLED = bool(DASHBOARD_USER and DASHBOARD_PASSWORD_HASH)


def require_login(f):
    """Decorator to require login if AUTH_ENABLED."""
    from functools import wraps

    @wraps(f)
    def decorated(*args, **kwargs):
        if AUTH_ENABLED and not session.get("user_id"):
            return redirect(url_for("login", next=request.url))
        return f(*args, **kwargs)
    return decorated


def require_api_key(f):
    """Decorator to require API key if BRAIN_API_KEY is set."""
    from functools import wraps

    @wraps(f)
    def decorated(*args, **kwargs):
        if BRAIN_API_KEY:
            provided = request.headers.get("X-API-Key", "") or request.args.get("api_key", "")
            if not provided or not hmac.compare_digest(provided, BRAIN_API_KEY):
                return jsonify({"error": "Invalid or missing API key"}), 401
        return f(*args, **kwargs)
    return decorated


def get_conn():
    return get_connection()


@app.route("/login", methods=["GET", "POST"])
@limiter.limit("5 per minute", methods=["POST"])
def login():
    if not AUTH_ENABLED:
        return redirect(url_for("index"))
    if request.method == "POST":
        username = request.form.get("username", "")
        password = request.form.get("password", "")
        if username == DASHBOARD_USER and check_password_hash(DASHBOARD_PASSWORD_HASH, password):
            session["user_id"] = username
            session.permanent = True
            app.permanent_session_lifetime = timedelta(days=7)
            next_url = request.args.get("next")
            if not _is_safe_url(next_url):
                next_url = url_for("index")
            return redirect(next_url)
        flash("Invalid username or password", "error")
    return render_template("login.html")


@app.route("/logout")
def logout():
    session.pop("user_id", None)
    flash("Logged out successfully", "success")
    return redirect(url_for("login" if AUTH_ENABLED else "index"))


def get_current_user_id():
    """Ambil user_id dari session (username saat AUTH_ENABLED)."""
    return session.get("user_id")


@app.route("/health")
def health():
    """Health endpoint — returns DB status and record counts."""
    conn = get_conn()
    try:
        stats = get_db_stats(conn)
        return jsonify({
            "status": "ok",
            "database": str(DB_PATH),
            "tables": stats,
        })
    except Exception as e:
        return jsonify({"status": "error", "error": str(e)}), 500
    finally:
        conn.close()


# ----------------------------------------------------------------------
# Routes
# ----------------------------------------------------------------------

@app.route("/")
@require_login
def index():
    conn = get_conn()
    try:
        # Quick stats
        contacts = search_contacts(conn, "", limit=10000)
        notes = search_notes(conn, "", limit=10000)
        knowledge = search_knowledge(conn, "", limit=10000)
        entities = search_entities(conn, "", limit=10000)

        stats = {
            "contacts": len(contacts),
            "notes": len(notes),
            "knowledge": len(knowledge),
            "entities": len(entities),
        }
        return render_template("dashboard.html", stats=stats, page="dashboard")
    finally:
        conn.close()


# ---- Contacts ----

@app.route("/contacts")
@require_login
def list_contacts():
    q = request.args.get("q", "")
    user_id = get_current_user_id()
    conn = get_conn()
    try:
        if user_id:
            contacts = [dict(r) for r in get_contacts_by_owner(conn, user_id, limit=100)]
        else:
            contacts = [dict(r) for r in search_contacts(conn, q, limit=100)]
        return render_template("contacts.html", contacts=contacts, query=q, page="contacts")
    finally:
        conn.close()


@app.route("/contacts/add", methods=["GET", "POST"])
@require_login
def add_contact():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip() or None
        phone = request.form.get("phone", "").strip() or None
        company = request.form.get("company", "").strip() or None
        role = request.form.get("role", "").strip() or None
        notes = request.form.get("notes", "").strip() or None
        if not name:
            flash("Name is required", "error")
            return redirect(url_for("add_contact"))
        conn = get_conn()
        try:
            user_id = get_current_user_id()
            # Deteksi prompt-injection pada nama dan catatan
            combined = f"{name} {notes or ''}"
            is_inj, risk = detect_prompt_injection(combined)
            prov_label = "prompt-injection" if is_inj else "manual"
            cid = upsert_contact_with_owner(
                conn, name=name, email=email, phone=phone,
                company=company, role=role,
                source="web", context_notes=notes,
                owner_user_id=user_id,
            )
            label_item(conn, "contact", cid,
                       provenance_label=prov_label, injection_risk=risk)
            flash("Contact added successfully", "success")
        finally:
            conn.close()
        return redirect(url_for("list_contacts"))
    return render_template("contact_add.html", page="contacts")


@app.route("/contacts/<int:contact_id>/edit", methods=["GET", "POST"])
@require_login
def edit_contact(contact_id):
    user_id = get_current_user_id()
    conn = get_conn()
    try:
        from brain_db import get_contact
        contact = get_contact(conn, contact_id)
        if not contact:
            flash("Contact not found", "error")
            return redirect(url_for("list_contacts"))
        contact = dict(contact)

        # Ownership enforcement: user hanya boleh edit kontak miliknya
        if user_id and contact.get("owner_user_id") not in (None, user_id):
            flash("You do not own this contact", "error")
            return redirect(url_for("list_contacts"))

        if request.method == "POST":
            name = request.form.get("name", "").strip() or None
            notes = request.form.get("notes", "").strip() or None
            combined = f"{name or ''} {notes or ''}"
            is_inj, risk = detect_prompt_injection(combined)
            prov_label = "prompt-injection" if is_inj else "manual"
            cid = upsert_contact_with_owner(
                conn, name=name, email=request.form.get("email", "").strip() or None,
                phone=request.form.get("phone", "").strip() or None,
                company=request.form.get("company", "").strip() or None,
                role=request.form.get("role", "").strip() or None,
                source="web", context_notes=notes,
                owner_user_id=user_id,
            )
            label_item(conn, "contact", cid,
                       provenance_label=prov_label, injection_risk=risk)
            flash("Contact updated successfully", "success")
            return redirect(url_for("list_contacts"))

        return render_template("contact_edit.html", contact=contact, page="contacts")
    finally:
        conn.close()


@app.route("/contacts/<int:contact_id>/delete", methods=["POST"])
@require_login
def delete_contact(contact_id):
    user_id = get_current_user_id()
    conn = get_conn()
    try:
        from brain_db import get_contact
        contact = get_contact(conn, contact_id)
        if not contact:
            flash("Contact not found", "error")
            return redirect(url_for("list_contacts"))
        if user_id and contact.get("owner_user_id") not in (None, user_id):
            flash("You do not own this contact", "error")
            return redirect(url_for("list_contacts"))
        if delete_contact_db(conn, contact_id):
            flash("Contact deleted successfully", "success")
        else:
            flash("Contact not found", "error")
    finally:
        conn.close()
    return redirect(url_for("list_contacts"))


# ---- Notes ----

@app.route("/notes")
@require_login
def list_notes():
    q = request.args.get("q", "")
    user_id = get_current_user_id()
    conn = get_conn()
    try:
        if user_id:
            notes = [dict(r) for r in get_notes_by_owner(conn, user_id, limit=100)]
        else:
            notes = [dict(r) for r in search_notes(conn, q, limit=100)]
        return render_template("notes.html", notes=notes, query=q, page="notes")
    finally:
        conn.close()


@app.route("/notes/add", methods=["GET", "POST"])
@require_login
def add_note():
    if request.method == "POST":
        title = request.form.get("title", "").strip()
        content = request.form.get("content", "").strip()
        tags = request.form.get("tags", "").strip()
        tag_list = [t.strip() for t in tags.split(",") if t.strip()] if tags else None
        user_id = get_current_user_id()
        conn = get_conn()
        try:
            combined = f"{title} {content}"
            is_inj, risk = detect_prompt_injection(combined)
            prov_label = "prompt-injection" if is_inj else "manual"
            nid = upsert_note_with_owner(
                conn, filename=request.form.get("filename", "web-note.md"),
                file_path="web",
                title=title or "(no title)",
                content=content,
                tags=tag_list,
                owner_user_id=user_id,
            )
            label_item(conn, "note", nid,
                       provenance_label=prov_label, injection_risk=risk)
            flash("Note added successfully", "success")
        finally:
            conn.close()
        return redirect(url_for("list_notes"))
    return render_template("note_add.html", page="notes")


@app.route("/notes/<int:note_id>/edit", methods=["GET", "POST"])
@require_login
def edit_note(note_id):
    user_id = get_current_user_id()
    conn = get_conn()
    try:
        from brain_db import get_contact  # noqa
        # We need a get_note helper; fetch directly
        cur = conn.cursor()
        cur.execute("SELECT * FROM notes WHERE id = ?", (note_id,))
        row = cur.fetchone()
        if not row:
            flash("Note not found", "error")
            return redirect(url_for("list_notes"))
        note = dict(row)

        # Ownership enforcement
        if user_id and note.get("owner_user_id") not in (None, user_id):
            flash("You do not own this note", "error")
            return redirect(url_for("list_notes"))

        if request.method == "POST":
            title = request.form.get("title", "").strip() or None
            content = request.form.get("content", "").strip() or None
            summary = request.form.get("summary", "").strip() or None
            tags = [t.strip() for t in request.form.get("tags", "").split(",") if t.strip()] or None
            combined = f"{title or ''} {content or ''}"
            is_inj, risk = detect_prompt_injection(combined)
            prov_label = "prompt-injection" if is_inj else "manual"
            nid = upsert_note_with_owner(
                conn, filename=note.get("filename", "web-note.md"),
                file_path="web",
                title=title,
                content=content,
                tags=tags,
                owner_user_id=user_id,
            )
            label_item(conn, "note", nid,
                       provenance_label=prov_label, injection_risk=risk)
            flash("Note updated successfully", "success")
            return redirect(url_for("list_notes"))

        return render_template("note_edit.html", note=note, page="notes")
    finally:
        conn.close()


@app.route("/notes/<int:note_id>/delete", methods=["POST"])
@require_login
def delete_note(note_id):
    user_id = get_current_user_id()
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("SELECT * FROM notes WHERE id = ?", (note_id,))
        row = cur.fetchone()
        if not row:
            flash("Note not found", "error")
            return redirect(url_for("list_notes"))
        note = dict(row)
        if user_id and note.get("owner_user_id") not in (None, user_id):
            flash("You do not own this note", "error")
            return redirect(url_for("list_notes"))
        if delete_note_db(conn, note_id):
            flash("Note deleted successfully", "success")
        else:
            flash("Note not found", "error")
    finally:
        conn.close()
    return redirect(url_for("list_notes"))


# ---- Search ----

@app.route("/search")
@require_login
def search():
    q = request.args.get("q", "")
    page = int(request.args.get("page", "1") or "1")
    per_page = int(request.args.get("per_page", "20") or "20")
    offset = (page - 1) * per_page
    conn = get_conn()
    try:
        results = global_search(conn, q, limit=per_page) if q else None
        # Add pagination counts
        if q and results:
            results["_pagination"] = {
                "page": page,
                "per_page": per_page,
                "offset": offset,
                "total_contacts": count_contacts(conn, q),
                "total_notes": count_notes(conn, q),
                "total_knowledge": count_knowledge(conn, q),
                "total_emails": count_emails(conn, q),
                "total_events": count_events(conn),
            }
        semantic = semantic_search(conn, q, limit=10) if q else None
        graph = query_knowledge_graph(conn, q, limit=10) if q else None
        return render_template("search.html", query=q, results=results,
                                      semantic=semantic, graph=graph, page="search")
    finally:
        conn.close()


# ---- Knowledge Graph ----

@app.route("/graph")
@require_login
def graph():
    q = request.args.get("q", "")
    conn = get_conn()
    try:
        graph = query_knowledge_graph(conn, q, limit=50) if q else {
            "entities": [dict(r) for r in search_entities(conn, "", limit=50)],
            "relations": [],
        }
        # Get all relations for full graph view if no query
        if not q:
            conn2 = get_conn()
            try:
                from brain_db import get_entity_relations
                all_rels = []
                seen = set()
                for e in graph["entities"]:
                    rels = get_entity_relations(conn2, e["id"], limit=100)
                    for r in rels:
                        key = (r["source_entity_id"], r["target_entity_id"], r["relation_type"])
                        if key not in seen:
                            seen.add(key)
                            all_rels.append(r)
                graph["relations"] = all_rels
            finally:
                conn2.close()
        return render_template("graph.html", graph=graph, query=q, page="graph")
    finally:
        conn.close()


# ---- Export / Import ----

@app.route("/export")
@require_login
@require_api_key
def export():
    fmt = request.args.get("format", "json")
    conn = get_conn()
    try:
        if fmt == "markdown":
            content = export_brain_markdown(conn)
            return content, 200, {"Content-Type": "text/markdown; charset=utf-8",
                                  "Content-Disposition": "attachment; filename=brain-export.md"}
        else:
            data = export_brain_json(conn)
            return jsonify(data)
    finally:
        conn.close()


@app.route("/import", methods=["GET", "POST"])
@require_login
@require_api_key
def import_brain():
    if request.method == "POST":
        if "file" not in request.files:
            flash("No file uploaded", "error")
            return redirect(url_for("import_brain"))
        file = request.files["file"]
        if file.filename == "":
            flash("No file selected", "error")
            return redirect(url_for("import_brain"))

        try:
            content = file.read().decode("utf-8")
            data = json.loads(content)
        except Exception as e:
            flash(f"Invalid JSON: {e}", "error")
            return redirect(url_for("import_brain"))

        conn = get_conn()
        try:
            result = import_brain_json(conn, data)
            flash(f"Import complete: {result['contacts']} contacts, "
                  f"{result['notes']} notes, {result['knowledge_entries']} knowledge entries, "
                  f"{result['entities']} entities, {result['entity_relations']} relations", "success")
        finally:
            conn.close()
        return redirect(url_for("index"))

    return render_template("import.html", page="import")


# ---- Conversations ----

@app.route("/conversations")
@require_login
def conversations():
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("SELECT * FROM conversations ORDER BY created_at DESC LIMIT 50")
        rows = [dict(r) for r in cur.fetchall()]
        return render_template("conversations.html", conversations=rows, page="conversations")
    finally:
        conn.close()


# ---- API endpoints (for programmatic access) ----

@app.route("/api/search")
@require_api_key
def api_search():
    q = request.args.get("q", "")
    conn = get_conn()
    try:
        results = global_search(conn, q, limit=20) if q else {}
        return jsonify(results)
    finally:
        conn.close()


# ----------------------------------------------------------------------
# HTML Templates
# ----------------------------------------------------------------------

BASE_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Personal Brain Dashboard</title>
    <style>
        * { box-sizing: border-box; }
        body { font-family: system-ui, -apple-system, sans-serif; background: #0f0f0f; color: #e0e0e0; margin: 0; padding: 0; }
        header { background: #1a1a1a; border-bottom: 1px solid #333; padding: 1rem 2rem; }
        header h1 { margin: 0; font-size: 1.25rem; color: #3b82f6; }
        nav { display: flex; gap: 1.5rem; margin-top: 0.5rem; }
        nav a { color: #888; text-decoration: none; font-size: 0.9rem; }
        nav a:hover, nav a.active { color: #3b82f6; }
        .container { max-width: 1200px; margin: 0 auto; padding: 1.5rem; }
        .stats { display: flex; gap: 1rem; margin-bottom: 1.5rem; }
        .stat-card { background: #1a1a1a; border: 1px solid #333; border-radius: 8px; padding: 1rem; flex: 1; }
        .stat-card h3 { margin: 0 0 0.5rem 0; font-size: 0.85rem; color: #888; }
        .stat-card .value { font-size: 2rem; color: #3b82f6; }
        .flash { padding: 1rem; border-radius: 6px; margin-bottom: 1rem; }
        .flash.success { background: #064e3b; color: #d1fae5; border: 1px solid #10b981; }
        .flash.error { background: #7f1d1d; color: #fecaca; border: 1px solid #ef4444; }
        table { width: 100%; border-collapse: collapse; background: #1a1a1a; border: 1px solid #333; border-radius: 8px; overflow: hidden; }
        th, td { padding: 0.75rem; text-align: left; border-bottom: 1px solid #333; }
        th { background: #111; color: #888; font-size: 0.85rem; }
        tr:hover { background: #161616; }
        .btn { background: #3b82f6; color: white; border: none; padding: 0.5rem 1rem; border-radius: 6px; cursor: pointer; text-decoration: none; font-size: 0.9rem; }
        .btn:hover { background: #2563eb; }
        .btn-danger { background: #ef4444; }
        .btn-danger:hover { background: #dc2626; }
        .btn-sm { padding: 0.25rem 0.5rem; font-size: 0.8rem; }
        .form-group { margin-bottom: 1rem; }
        .form-group label { display: block; margin-bottom: 0.25rem; color: #888; font-size: 0.9rem; }
        .form-group input, .form-group textarea { width: 100%; background: #111; border: 1px solid #333; color: #e0e0e0; padding: 0.5rem; border-radius: 4px; }
        .form-group textarea { min-height: 100px; }
        .search-box { margin-bottom: 1.5rem; }
        .search-box input[type="text"] { width: 100%; max-width: 500px; padding: 0.6rem; border: 1px solid #333; border-radius: 6px; background: #111; color: #e0e0e0; font-size: 1rem; }
        .badge { display: inline-block; padding: 0.125rem 0.5rem; border-radius: 4px; font-size: 0.75rem; background: #333; color: #888; }
        .badge-person { background: #3b82f6; color: white; }
        .badge-company { background: #10b981; color: white; }
        .badge-concept { background: #8b5cf6; color: white; }
        .relation-line { font-family: monospace; font-size: 0.85rem; }
        .empty { text-align: center; color: #888; padding: 2rem; }
    </style>
</head>
<body>
    <header>
        <h1>🧠 Personal Brain Dashboard</h1>
        <nav>
            <a href="/" class="{% if page=='dashboard' %}active{% endif %}">Dashboard</a>
            <a href="/contacts" class="{% if page=='contacts' %}active{% endif %}">Contacts</a>
            <a href="/notes" class="{% if page=='notes' %}active{% endif %}">Notes</a>
            <a href="/search" class="{% if page=='search' %}active{% endif %}">Search</a>
            <a href="/graph" class="{% if page=='graph' %}active{% endif %}">Knowledge Graph</a>
            <a href="/conversations" class="{% if page=='conversations' %}active{% endif %}">Conversations</a>
            <a href="/export?format=json" class="{% if page=='import' %}active{% endif %}">Export JSON</a>
            <a href="/import" class="{% if page=='import' %}active{% endif %}">Import</a>
        </nav>
    </header>
    <div class="container">
        {% block content %}{% endblock %}
    </div>
</body>
</html>"""

DASHBOARD_HTML = BASE_HTML.replace("</div>", """
    <div class="stats">
        <div class="stat-card"><h3>Contacts</h3><div class="value">{{ stats.contacts }}</div></div>
        <div class="stat-card"><h3>Notes</h3><div class="value">{{ stats.notes }}</div></div>
        <div class="stat-card"><h3>Knowledge Entries</h3><div class="value">{{ stats.knowledge }}</div></div>
        <div class="stat-card"><h3>Graph Entities</h3><div class="value">{{ stats.entities }}</div></div>
    </div>
    <div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(300px, 1fr)); gap: 1.5rem;">
        <div class="stat-card">
            <h3 style="margin-top:0">Quick Actions</h3>
            <a href="/contacts/add" class="btn">Add Contact</a><br>
            <a href="/notes/add" class="btn" style="margin-top:0.5rem">Add Note</a><br>
            <a href="/search" class="btn" style="margin-top:0.5rem">Search Brain</a><br>
            <a href="/graph" class="btn" style="margin-top:0.5rem">View Knowledge Graph</a>
        </div>
        <div class="stat-card">
            <h3 style="margin-top:0">About</h3>
            <p style="font-size:0.9rem; color:#888">
                Personal Brain is your local, searchable knowledge base.<br>
                All data stored in SQLite on your computer.<br>
                No server required.<br>
                <small>API key for sharing: set BRAIN_API_KEY env var.</small>
            </p>
        </div>
    </div>
</div>
""")

CONTACTS_HTML = BASE_HTML.replace("</div>", """
    <div class="search-box">
        <form method="get" action="/contacts">
            <input type="text" name="q" value="{{ query }}" placeholder="Search contacts...">
        </form>
    </div>
    <a href="/contacts/add" class="btn">+ Add Contact</a>
    {% if contacts %}
    <table>
        <thead><tr><th>Name</th><th>Email</th><th>Company</th><th>Role</th><th>Source</th><th>Actions</th></tr></thead>
        <tbody>
        {% for c in contacts %}
        <tr>
            <td><strong>{{ c.name }}</strong></td>
            <td>{{ c.email or '-' }}</td>
            <td>{{ c.company or '-' }}</td>
            <td>{{ c.role or '-' }}</td>
            <td><span class="badge">{{ c.source }}</span></td>
            <td>
                <a href="/contacts/{{ c.id }}/edit" class="btn btn-sm">Edit</a>
                <form method="POST" action="/contacts/{{ c.id }}/delete" style="display:inline" onsubmit="return confirm('Delete this contact?')">
                    <button type="submit" class="btn btn-danger btn-sm">Delete</button>
                </form>
            </td>
        </tr>
        {% endfor %}
        </tbody>
    </table>
    {% else %}
    <div class="empty">No contacts found. Add your first contact.</div>
    {% endif %}
</div>
""")

CONTACT_ADD_HTML = BASE_HTML.replace("</div>", """
    <h2>Add Contact</h2>
    <form method="POST">
        <div class="form-group">
            <label>Name *</label>
            <input type="text" name="name" required>
        </div>
        <div class="form-group">
            <label>Email</label>
            <input type="email" name="email">
        </div>
        <div class="form-group">
            <label>Phone</label>
            <input type="text" name="phone">
        </div>
        <div class="form-group">
            <label>Company</label>
            <input type="text" name="company">
        </div>
        <div class="form-group">
            <label>Role</label>
            <input type="text" name="role">
        </div>
        <div class="form-group">
            <label>Notes</label>
            <textarea name="notes"></textarea>
        </div>
        <button type="submit" class="btn">Save Contact</a>
    </form>
</div>
""")

CONTACT_EDIT_HTML = BASE_HTML.replace("</div>", """
    <h2>Edit Contact: {{ contact.name }}</h2>
    <form method="POST">
        <div class="form-group">
            <label>Name</label>
            <input type="text" name="name" value="{{ contact.name }}">
        </div>
        <div class="form-group">
            <label>Email</label>
            <input type="email" name="email" value="{{ contact.email or '' }}">
        </div>
        <div class="form-group">
            <label>Phone</label>
            <input type="text" name="phone" value="{{ contact.phone or '' }}">
        </div>
        <div class="form-group">
            <label>Company</label>
            <input type="text" name="company" value="{{ contact.company or '' }}">
        </div>
        <div class="form-group">
            <label>Role</label>
            <input type="text" name="role" value="{{ contact.role or '' }}">
        </div>
        <div class="form-group">
            <label>Notes</label>
            <textarea name="notes">{{ contact.context_notes or '' }}</textarea>
        </div>
        <button type="submit" class="btn">Save Changes</a>
    </form>
</div>
""")

NOTES_HTML = BASE_HTML.replace("</div>", """
    <div class="search-box">
        <form method="get" action="/notes">
            <input type="text" name="q" value="{{ query }}" placeholder="Search notes...">
        </form>
    </div>
    <a href="/notes/add" class="btn">+ Add Note</a>
    {% if notes %}
    <table>
        <thead><tr><th>Title</th><th>File</th><th>Summary</th><th>Tags</th><th>Actions</th></tr></thead>
        <tbody>
        {% for n in notes %}
        <tr>
            <td><strong>{{ n.title or '(no title)' }}</strong></td>
            <td>{{ n.filename or '-' }}</td>
            <td>{{ (n.summary or '')[:80] }}{% if n.summary and n.summary|length > 80 %}...{% endif %}</td>
            <td>{{ n.tags or '-' }}</td>
            <td>
                <a href="/notes/{{ n.id }}/edit" class="btn btn-sm">Edit</a>
                <form method="POST" action="/notes/{{ n.id }}/delete" style="display:inline" onsubmit="return confirm('Delete this note?')">
                    <button type="submit" class="btn btn-danger btn-sm">Delete</button>
                </form>
            </td>
        </tr>
        {% endfor %}
        </tbody>
    </table>
    {% else %}
    <div class="empty">No notes found.</div>
    {% endif %}
</div>
""")

NOTE_ADD_HTML = BASE_HTML.replace("</div>", """
    <h2>Add Note</h2>
    <form method="POST">
        <div class="form-group">
            <label>Filename</label>
            <input type="text" name="filename" value="web-note.md">
        </div>
        <div class="form-group">
            <label>Title</label>
            <input type="text" name="title">
        </div>
        <div class="form-group">
            <label>Content</label>
            <textarea name="content"></textarea>
        </div>
        <div class="form-group">
            <label>Summary</label>
            <input type="text" name="summary">
        </div>
        <div class="form-group">
            <label>Tags (comma-separated)</label>
            <input type="text" name="tags" placeholder="tag1, tag2">
        </div>
        <button type="submit" class="btn">Save Note</a>
    </form>
</div>
""")

NOTE_EDIT_HTML = BASE_HTML.replace("</div>", """
    <h2>Edit Note: {{ note.title or '(no title)' }}</h2>
    <form method="POST">
        <div class="form-group">
            <label>Filename</label>
            <input type="text" name="filename" value="{{ note.filename }}">
        </div>
        <div class="form-group">
            <label>Title</label>
            <input type="text" name="title" value="{{ note.title or '' }}">
        </div>
        <div class="form-group">
            <label>Content</label>
            <textarea name="content">{{ note.content or '' }}</textarea>
        </div>
        <div class="form-group">
            <label>Summary</label>
            <input type="text" name="summary" value="{{ note.summary or '' }}">
        </div>
        <div class="form-group">
            <label>Tags (comma-separated)</label>
            <input type="text" name="tags" value="{{ note.tags or '' }}">
        </div>
        <button type="submit" class="btn">Save Changes</a>
    </form>
</div>
""")

SEARCH_HTML = BASE_HTML.replace("</div>", """
    <div class="search-box">
        <form method="get" action="/search">
            <input type="text" name="q" value="{{ query }}" placeholder="Search brain...">
        </form>
    </div>
    {% if query %}
    <h3>Results for: "{{ query }}"</h3>

    {% if results.contacts %}
    <h4>Contacts ({{ results.contacts|length }})</h4>
    <ul>
    {% for c in results.contacts %}
    <li><strong>{{ c.name }}</strong> - {{ c.email or 'no email' }} {% if c.company %}({{ c.company }}){% endif %}</li>
    {% endfor %}
    </ul>
    {% endif %}

    {% if results.notes %}
    <h4>Notes ({{ results.notes|length }})</h4>
    <ul>
    {% for n in results.notes %}
    <li><strong>{{ n.title or n.filename }}</strong> - {{ (n.summary or '')[:100] }}</li>
    {% endfor %}
    </ul>
    {% endif %}

    {% if results.knowledge %}
    <h4>Knowledge ({{ results.knowledge|length }})</h4>
    <ul>
    {% for k in results.knowledge %}
    <li><strong>{{ k.title }}</strong> - {{ k.content[:100] }}</li>
    {% endfor %}
    </ul>
    {% endif %}

    {% if semantic %}
    <h4>Semantic Matches ({{ semantic|length }})</h4>
    <ul>
    {% for s in semantic %}
    <li>[{{ s.source_type }}] {{ s.content_chunk[:150] }} (relevance: {{ s.similarity }})</li>
    {% endfor %}
    </ul>
    {% endif %}

    {% if graph and graph.relations %}
    <h4>Knowledge Graph Relations</h4>
    <ul>
    {% for r in graph.relations %}
    <li>{{ r.source_name }} →{{ r.relation_type }}→ {{ r.target_name }}</li>
    {% endfor %}
    </ul>
    {% endif %}

    {% if not results.contacts and not results.notes and not results.knowledge and not semantic and (not graph or not graph.relations) %}
    <div class="empty">No results found.</div>
    {% endif %}
    {% endif %}
</div>
""")

GRAPH_HTML = BASE_HTML.replace("</div>", """
    <div class="search-box">
        <form method="get" action="/graph">
            <input type="text" name="q" value="{{ query }}" placeholder="Search knowledge graph...">
        </form>
    </div>
    {% if graph.entities %}
    <h3>Entities ({{ graph.entities|length }})</h3>
    <table>
        <thead><tr><th>Name</th><th>Type</th><th>Source</th></tr></thead>
        <tbody>
        {% for e in graph.entities %}
        <tr>
            <td><strong>{{ e.name }}</strong></td>
            <td><span class="badge badge-{{ e.entity_type }}">{{ e.entity_type }}</span></td>
            <td>{{ e.source }}</td>
        </tr>
        {% endfor %}
        </tbody>
    </table>
    {% endif %}

    {% if graph.relations %}
    <h3>Relations ({{ graph.relations|length }})</h3>
    <table>
        <thead><tr><th>Source</th><th>Relation</th><th>Target</th><th>Confidence</th></tr></thead>
        <tbody>
        {% for r in graph.relations %}
        <tr>
            <td><strong>{{ r.source_name }}</strong></td>
            <td>{{ r.relation_type }}</td>
            <td><strong>{{ r.target_name }}</strong></td>
            <td>{{ "%.2f"|format(r.confidence) }}</td>
        </tr>
        {% endfor %}
        </tbody>
    </table>
    {% endif %}

    {% if not graph.entities and not graph.relations %}
    <div class="empty">No knowledge graph data found.</div>
    {% endif %}
</div>
""")

IMPORT_HTML = BASE_HTML.replace("</div>", """
    <h2>Import Brain Data</h2>
    <p>Upload a JSON file exported from another brain instance.</p>
    <form method="POST" enctype="multipart/form-data">
        <div class="form-group">
            <label>JSON File</label>
            <input type="file" name="file" accept=".json" required>
        </div>
        <button type="submit" class="btn">Import</a>
    </form>
</div>
""")

LOGIN_HTML = BASE_HTML.replace("</div>", """
    <h2>Login</h2>
    <form method="POST">
        <div class="form-group">
            <label>Username</label>
            <input type="text" name="username" required>
        </div>
        <div class="form-group">
            <label>Password</label>
            <input type="password" name="password" required>
        </div>
        <button type="submit" class="btn">Login</button>
    </form>
    <p><small>Set BRAIN_DASHBOARD_USER and BRAIN_DASHBOARD_PASSWORD to enable login.</small></p>
</div>
""")

CONVERSATIONS_HTML = BASE_HTML.replace("</div>", """
    <h2>Conversation History</h2>
    {% if conversations %}
    <table>
        <thead><tr><th>Time</th><th>User Message</th><th>Agent Response</th><th>Context Used</th></tr></thead>
        <tbody>
        {% for c in conversations %}
        <tr>
            <td>{{ c.created_at }}</td>
            <td>{{ c.user_message[:100] }}{% if c.user_message|length > 100 %}...{% endif %}</td>
            <td>{{ c.agent_response[:100] }}{% if c.agent_response|length > 100 %}...{% endif %}</td>
            <td>{{ c.context_used or '[]' }}</td>
        </tr>
        {% endfor %}
        </tbody>
    </table>
    {% else %}
    <div class="empty">No conversations yet.</div>
    {% endif %}
</div>
""")


def main():
    parser = argparse.ArgumentParser(description="Brain Web Dashboard")
    parser.add_argument("--host", default="127.0.0.1", help="Host (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=5000, help="Port (default: 5000)")
    parser.add_argument("--debug", action="store_true", help="Debug mode")
    args = parser.parse_args()

    logger.info(f"Starting Brain Web Dashboard at http://{args.host}:{args.port}")
    if BRAIN_API_KEY:
        logger.info("API key protection enabled. Set X-API-Key header or ?api_key= parameter.")
    else:
        logger.warning("No BRAIN_API_KEY set. Dashboard is open (local only).")

    app.run(host=args.host, port=args.port, debug=args.debug)


if __name__ == "__main__":
    main()