# admin.py
import os, sqlite3
from datetime import datetime
from flask import Blueprint, g, request, Response, render_template_string, redirect, url_for

# --- Admin config (set via env on Render/local) ---
ADMIN_USER = os.environ.get("ADMIN_USER", "admin")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "change-me-now")  # CHANGE THIS IN RENDER
ADMIN_ALLOWLIST = [ip.strip() for ip in os.environ.get("ADMIN_ALLOWLIST", "").split(",") if ip.strip()]
DB_PATH = os.environ.get("QA_DB_PATH", "qa.sqlite3")

admin_bp = Blueprint("admin", __name__)

# --- DB helper reusing the same SQLite file ---
def get_db():
    if "admin_db" not in g:
        g.admin_db = sqlite3.connect(DB_PATH, detect_types=sqlite3.PARSE_DECLTYPES)
        g.admin_db.row_factory = sqlite3.Row
    return g.admin_db

@admin_bp.teardown_request
def close_db(error=None):
    db = g.pop("admin_db", None)
    if db is not None:
        db.close()

# --- Basic Auth ---
def check_auth(auth_header: str) -> bool:
    if not auth_header or not auth_header.lower().startswith("basic "):
        return False
    try:
        import base64
        decoded = base64.b64decode(auth_header.split(None, 1)[1]).decode("utf-8")
        username, password = decoded.split(":", 1)
    except Exception:
        return False
    return (username == ADMIN_USER and password == ADMIN_PASSWORD)

def require_auth():
    return Response(
        "Authentication required", 401,
        {"WWW-Authenticate": 'Basic realm="Admin", charset="UTF-8"'}
    )

# --- Optional IP allowlist ---
def allowed_ip():
    if not ADMIN_ALLOWLIST:
        return True
    ip = request.headers.get("X-Forwarded-For", request.remote_addr or "")
    ip = ip.split(",")[0].strip()
    return ip in ADMIN_ALLOWLIST

@admin_bp.before_request
def gate():
    if not allowed_ip():
        return Response("Forbidden", 403)
    if not check_auth(request.headers.get("Authorization")):
        return require_auth()

# --- Minimal admin layout ---
ADMIN_BASE = """
<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Admin · Debate</title>
    <script src="https://cdn.tailwindcss.com"></script>
  </head>
  <body class="bg-zinc-50 text-zinc-900">
    <div class="max-w-5xl mx-auto p-4">
      <header class="flex items-center justify-between mb-6">
        <h1 class="text-2xl font-bold">Admin · Debate</h1>
        <div class="text-sm text-zinc-500">{{ now }}</div>
      </header>
      {{ body|safe }}
    </div>
  </body>
</html>
"""

def render_admin(body_template: str, **context):
    inner = render_template_string(body_template, **context)
    return render_template_string(ADMIN_BASE, body=inner, now=datetime.utcnow())

@admin_bp.route("/")
def dashboard():
    db = get_db()
    q_count = db.execute("SELECT COUNT(*) FROM questions").fetchone()[0]
    a_count = db.execute("SELECT COUNT(*) FROM answers").fetchone()[0]
    v_count = db.execute("SELECT COUNT(*) FROM votes").fetchone()[0]
    suggestions_count = db.execute("SELECT COUNT(*) FROM suggestions").fetchone()[0]
    body = f"""
    <div class="grid grid-cols-1 md:grid-cols-4 gap-4">
      <div class="bg-white p-4 rounded-2xl shadow"><div class="text-sm text-zinc-500">Questions</div><div class="text-3xl font-bold">{q_count}</div></div>
      <div class="bg-white p-4 rounded-2xl shadow"><div class="text-sm text-zinc-500">Answers</div><div class="text-3xl font-bold">{a_count}</div></div>
      <div class="bg-white p-4 rounded-2xl shadow"><div class="text-sm text-zinc-500">Votes</div><div class="text-3xl font-bold">{v_count}</div></div>
      <div class="bg-white p-4 rounded-2xl shadow"><div class="text-sm text-zinc-500">Suggestions</div><div class="text-3xl font-bold">{suggestions_count}</div></div>
    </div>
    <div class="mt-6 flex gap-2">
      <a href="{url_for('admin.questions')}" class="px-3 py-2 rounded-xl border">Manage Questions</a>
      <a href="{url_for('admin.answers')}" class="px-3 py-2 rounded-xl border">Manage Answers</a>
      <a href="{url_for('admin.suggestions')}" class="px-3 py-2 rounded-xl border">Manage Suggestions</a>
    </div>
    """
    return render_admin(body)

@admin_bp.route("/questions")
def questions():
    db = get_db()
    rows = db.execute("""
      SELECT q.id, q.title, q.created_at,
             (SELECT COUNT(*) FROM answers a WHERE a.question_id=q.id) AS answers
      FROM questions q
      ORDER BY q.id DESC
      LIMIT 200
    """).fetchall()
    body = """
    <div class="bg-white p-4 rounded-2xl shadow">
      <h2 class="text-lg font-bold mb-3">Questions</h2>
      <table class="w-full text-sm">
        <thead><tr class="text-left text-zinc-500">
          <th class="py-2">ID</th><th>Title</th><th>Answers</th><th>Created</th><th></th>
        </tr></thead>
        <tbody>
        {% for r in rows %}
          <tr class="border-t">
            <td class="py-2">{{ r['id'] }}</td>
            <td class="pr-4">{{ r['title'] }}</td>
            <td>{{ r['answers'] }}</td>
            <td class="text-zinc-500">{{ r['created_at'] }}</td>
            <td>
              <a class="text-blue-600" href="{{ url_for('question', qid=r['id']) }}" target="_blank">view</a>
              <form method="post" action="{{ url_for('admin.delete_question', qid=r['id']) }}" style="display:inline" onsubmit="return confirm('Delete question and its answers?');">
                <button class="text-red-600 ml-2">delete</button>
              </form>
            </td>
          </tr>
        {% endfor %}
        </tbody>
      </table>
    </div>
    """
    return render_admin(body, rows=rows)

@admin_bp.route("/answers")
def answers():
    db = get_db()
    rows = db.execute("""
      SELECT a.id, a.body, a.name, a.created_at, a.question_id
      FROM answers a
      ORDER BY a.id DESC
      LIMIT 200
    """).fetchall()
    body = """
    <div class="bg-white p-4 rounded-2xl shadow">
      <h2 class="text-lg font-bold mb-3">Latest Answers</h2>
      <table class="w-full text-sm">
        <thead><tr class="text-left text-zinc-500">
          <th class="py-2">ID</th><th>Excerpt</th><th>Name</th><th>Question</th><th>Created</th><th></th>
        </tr></thead>
        <tbody>
        {% for r in rows %}
          <tr class="border-t">
            <td class="py-2">{{ r['id'] }}</td>
            <td class="pr-4">{{ r['body'][:120] }}{% if r['body']|length>120 %}…{% endif %}</td>
            <td>{{ r['name'] or 'Anonymous' }}</td>
            <td>#{{ r['question_id'] }}</td>
            <td class="text-zinc-500">{{ r['created_at'] }}</td>
            <td>
              <a class="text-blue-600" href="{{ url_for('question', qid=r['question_id']) }}" target="_blank">view</a>
              <form method="post" action="{{ url_for('admin.delete_answer', aid=r['id']) }}" style="display:inline" onsubmit="return confirm('Delete this answer?');">
                <button class="text-red-600 ml-2">delete</button>
              </form>
            </td>
          </tr>
        {% endfor %}
        </tbody>
      </table>
    </div>
    """
    return render_admin(body, rows=rows)

@admin_bp.route("/suggestions")
def suggestions():
    db = get_db()
    rows = db.execute("""
      SELECT id, body, contact, created_at
      FROM suggestions
      ORDER BY id DESC
      LIMIT 500
    """).fetchall()
    body = """
    <div class="bg-white p-4 rounded-2xl shadow">
      <h2 class="text-lg font-bold mb-3">Suggestions</h2>
      <table class="w-full text-sm">
        <thead><tr class="text-left text-zinc-500">
          <th class="py-2">ID</th><th>Excerpt</th><th>Contact</th><th>Created</th><th></th>
        </tr></thead>
        <tbody>
        {% for r in rows %}
          <tr class="border-t">
            <td class="py-2">{{ r['id'] }}</td>
            <td class="pr-4">{{ r['body'][:120] }}{% if r['body']|length>120 %}…{% endif %}</td>
            <td>{{ r['contact'] or '—' }}</td>
            <td class="text-zinc-500">{{ r['created_at'] }}</td>
            <td>
              <form method="post" action="{{ url_for('admin.delete_suggestion', sid=r['id']) }}" style="display:inline" onsubmit="return confirm('Delete this suggestion?');">
                <button class="text-red-600 ml-2">delete</button>
              </form>
            </td>
          </tr>
        {% endfor %}
        </tbody>
      </table>
    </div>
    """
    return render_admin(body, rows=rows)

# --- destructive actions (POST only) ---

@admin_bp.route("/delete-question/<int:qid>", methods=["POST"])
def delete_question(qid):
    db = get_db()
    db.execute("DELETE FROM answers WHERE question_id=?", (qid,))
    db.execute("DELETE FROM votes WHERE question_id=?", (qid,))
    db.execute("DELETE FROM questions WHERE id=?", (qid,))
    db.commit()
    return redirect(url_for("admin.questions"))

@admin_bp.route("/delete-answer/<int:aid>", methods=["POST"])
def delete_answer(aid):
    db = get_db()
    row = db.execute("SELECT question_id FROM answers WHERE id=?", (aid,)).fetchone()
    if row:
        qid = row["question_id"]
        db.execute("DELETE FROM votes WHERE answer_id=?", (aid,))
        db.execute("DELETE FROM answers WHERE id=?", (aid,))
        db.commit()
        return redirect(url_for("question", qid=qid))
    return redirect(url_for("admin.answers"))

@admin_bp.route("/delete-suggestion/<int:sid>", methods=["POST"])
def delete_suggestion(sid):
    db = get_db()
    db.execute("DELETE FROM suggestions WHERE id=?", (sid,))
    db.commit()
    return redirect(url_for("admin.suggestions"))