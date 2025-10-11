import os
import sqlite3
from datetime import datetime
import uuid, hashlib, hmac
from flask import Flask, g, render_template_string, request, redirect, url_for, abort, session
import bleach

# --- Config ---
DB_PATH = os.environ.get("QA_DB_PATH", "/var/data/qa.sqlite3")
RAW_SECRET = os.environ.get("FLASK_SECRET")
SECRET = RAW_SECRET.encode("utf-8") if isinstance(RAW_SECRET, str) else (RAW_SECRET or os.urandom(24))

ADMIN_PATH = os.environ.get("ADMIN_PATH", "/__debate-admin-92f1c3")

app = Flask(__name__)
app.config["SECRET_KEY"] = SECRET

# Allowed HTML tags/attributes for rich text (Quill output)
ALLOWED_TAGS = [
    "b", "i", "u", "em", "strong",
    "p", "br", "h1", "h2", "h3", "blockquote",
    "ul", "ol", "li", "span"
]
ALLOWED_ATTRS = {"span": ["style"]}

# --- DB helpers ---
def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH, detect_types=sqlite3.PARSE_DECLTYPES)
        g.db.row_factory = sqlite3.Row
    return g.db

@app.teardown_appcontext
def close_db(exception):
    db = g.pop("db", None)
    if db is not None:
        db.close()

def init_db():
    db = get_db()
    db.executescript(
        """
        CREATE TABLE IF NOT EXISTS questions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            body TEXT,
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS answers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            question_id INTEGER NOT NULL,
            body TEXT NOT NULL,
            name TEXT,
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (question_id) REFERENCES questions(id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_answers_qid ON answers(question_id);

        CREATE TABLE IF NOT EXISTS votes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            question_id INTEGER NOT NULL,
            answer_id INTEGER NOT NULL,
            anon_hash TEXT NOT NULL,
            ip_hash TEXT,
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(question_id, anon_hash)
        );
        CREATE INDEX IF NOT EXISTS idx_votes_answer ON votes(answer_id);
        CREATE INDEX IF NOT EXISTS idx_votes_question ON votes(question_id);
        CREATE INDEX IF NOT EXISTS idx_votes_q_ip ON votes(question_id, ip_hash);

        CREATE TABLE IF NOT EXISTS suggestions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            body TEXT NOT NULL,
            contact TEXT,
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        """
    )
    db.commit()

@app.before_request
def ensure_db():
    init_db()
    if 'anon_id' not in session:
        session['anon_id'] = uuid.uuid4().hex

# --- Helpers ---
def make_anon_hash(anon_id: str) -> str:
    return hmac.new(SECRET, (anon_id or "").encode("utf-8"), hashlib.sha256).hexdigest()

def client_ip() -> str:
    ip = request.headers.get("X-Forwarded-For", request.remote_addr or "")
    return ip.split(",")[0].strip()

def make_ip_hash(ip: str) -> str:
    if ":" in ip:
        parts = ip.split(":")
        net = ":".join(parts[:4])
    else:
        parts = ip.split(".")
        net = ".".join(parts[:3] + ["0"]) if len(parts) == 4 else ip
    return hmac.new(SECRET, net.encode("utf-8"), hashlib.sha256).hexdigest()

# --- Templates ---
BASE = """
<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Debate</title>
    <link rel="icon" type="image/png" href="/static/e.png">
    <script src="https://cdn.tailwindcss.com"></script>
    <link href="https://fonts.googleapis.com/css2?family=Lora:wght@400;600;700&amp;display=swap" rel="stylesheet">
    <link href="https://cdn.quilljs.com/1.3.7/quill.snow.css" rel="stylesheet">
    <script src="https://cdn.quilljs.com/1.3.7/quill.js"></script>
    <style>
      html, body, input, button, textarea { font-family: 'Lora', serif; }
      .ql-container { min-height: 180px; }
    </style>
  </head>
  <body class="bg-zinc-50 text-zinc-900">
    <div class="max-w-3xl mx-auto p-4">
      <header class="flex items-center justify-between mb-6">
        <a href="{{ url_for('index') }}" class="text-2xl font-bold">Debate</a>
        <div class="flex items-center gap-2">
          <a href="{{ url_for('ask') }}" class="inline-flex items-center px-3 py-2 rounded-xl bg-zinc-900 text-white hover:bg-zinc-800">Ask a question</a>
          <a href="{{ url_for('suggest') }}" class="inline-flex items-center px-3 py-2 rounded-xl border border-zinc-300 hover:bg-zinc-50">Suggestions</a>
        </div>
      </header>
      {{ body|safe }}
    </div>
  </body>
</html>
"""

INDEX = """
<div class="space-y-4">
  {% for q in questions %}
    <a href="{{ url_for('question', qid=q['id']) }}" class="block bg-white p-4 rounded-2xl shadow-sm hover:shadow-md transition">
      <h2 class="text-lg font-semibold">{{ q['title'] }}</h2>
      {% if q['body'] %}
        {% set preview = (q['body'] | striptags) %}
        <p class="text-sm text-zinc-600 mt-1">{{ preview[:180] }}{% if preview|length > 180 %}…{% endif %}</p>
      {% endif %}
      <div class="text-xs text-zinc-500 mt-2">{{ q['created_at'] }}</div>
    </a>
  {% else %}
    <p class="text-zinc-600">No questions yet.</p>
  {% endfor %}
</div>
"""

ASK = """
<div class="bg-white p-4 rounded-2xl shadow-sm">
  <form id="ask-form" method="post" class="space-y-3">
    <div>
      <label class="block text-sm text-zinc-600">Title <span class="text-red-600">*</span></label>
      <input name="title" required maxlength="180" class="w-full px-3 py-2 rounded-xl border border-zinc-200" />
    </div>
    <div>
      <label class="block text-sm text-zinc-600">Details (optional)</label>
      <input type="hidden" name="body" id="q-body" />
      <div id="q-editor" class="bg-white rounded-xl border border-zinc-200"></div>
    </div>
    <button type="submit" class="px-3 py-2 rounded-2xl bg-zinc-900 text-white">Post question</button>
  </form>

  <script>
    document.addEventListener('DOMContentLoaded', function () {
      var qEditor = new Quill('#q-editor', {
        theme: 'snow',
        placeholder: 'Add context, examples, or constraints…',
        modules: {
          toolbar: [
            [{ 'header': [1, 2, 3, false] }],
            [{ 'size': ['small', false, 'large', 'huge'] }],
            ['bold', 'italic', 'underline'],
            [{ 'list': 'ordered' }, { 'list': 'bullet' }],
            ['blockquote', 'clean']
          ]
        }
      });

      // Prevent empty posts and always update the hidden input
      var form = document.getElementById('ask-form');
      form.addEventListener('submit', function (e) {
        var html = qEditor.root.innerHTML.trim();
        if (html === '<p><br></p>' || html === '') {
          document.getElementById('q-body').value = '';
        } else {
          document.getElementById('q-body').value = html;
        }
      });
    });
  </script>
</div>
"""
QUESTION = """
<article class="bg-white p-5 rounded-2xl shadow-sm">
  <h1 class="text-2xl font-bold">{{ q['title'] }}</h1>
  {% if q['body'] %}
    <div class="prose prose-zinc max-w-none mt-2">{{ q['body'] | safe }}</div>
  {% endif %}
  <div class="text-xs text-zinc-500 mt-2">Asked {{ q['created_at'] }}</div>
</article>

<section class="mt-6">
  <h2 class="text-lg font-semibold mb-2">Answers ({{ answers|length }})</h2>
  <div class="space-y-3">
    {% for a in answers %}
      <div class="bg-white p-4 rounded-2xl shadow-sm">
        <div class="text-sm text-zinc-600">by {{ a['name'] or 'Anonymous' }}</div>
        <div class="prose prose-zinc max-w-none mt-1">{{ a['body'] | safe }}</div>
        <div class="text-xs text-zinc-500 mt-2">{{ a['created_at'] }}</div>
      </div>
    {% else %}
      <p class="text-zinc-600">No answers yet.</p>
    {% endfor %}
  </div>
</section>

<section class="mt-6 bg-white p-4 rounded-2xl shadow-sm">
  <h3 class="font-semibold">Add your answer</h3>
  <form id="answer-form" method="post" action="{{ url_for('answer', qid=q['id']) }}" class="space-y-3">
    <div>
      <label class="block text-sm text-zinc-600">Display name (optional)</label>
      <input name="name" maxlength="80" class="w-full px-3 py-2 rounded-xl border border-zinc-200" />
    </div>
    <div>
      <label class="block text-sm text-zinc-600">Your answer <span class="text-red-600">*</span></label>
      <input type="hidden" name="body" id="a-body" />
      <div id="a-editor" class="bg-white rounded-xl border border-zinc-200"></div>
    </div>
    <button type="submit" class="px-3 py-2 rounded-xl bg-zinc-900 text-white">Post answer</button>
  </form>

  <script>
    document.addEventListener('DOMContentLoaded', function () {
      var aEditor = new Quill('#a-editor', {
        theme: 'snow',
        placeholder: 'Write your answer…',
        modules: {
          toolbar: [
            [{ 'header': [1, 2, 3, false] }],
            [{ 'size': ['small', false, 'large', 'huge'] }],
            ['bold', 'italic', 'underline'],
            [{ 'list': 'ordered' }, { 'list': 'bullet' }],
            ['blockquote', 'clean']
          ]
        }
      });

      var form = document.getElementById('answer-form');
      form.addEventListener('submit', function (e) {
        var html = aEditor.root.innerHTML.trim();
        if (html === '<p><br></p>' || html === '') {
          document.getElementById('a-body').value = '';
        } else {
          document.getElementById('a-body').value = html;
        }
      });
    });
  </script>
</section>
"""
# --- Routes ---
@app.route("/")
def index():
    db = get_db()
    qs = db.execute("SELECT id, title, body, created_at FROM questions ORDER BY id DESC LIMIT 50").fetchall()
    body = render_template_string(INDEX, questions=qs)
    return render_template_string(BASE, body=body)

@app.route("/ask", methods=["GET", "POST"])
def ask():
    if request.method == "POST":
        title = (request.form.get("title") or "").strip()
        raw_body = (request.form.get("body") or "").strip()
        body = bleach.clean(raw_body, tags=ALLOWED_TAGS, attributes=ALLOWED_ATTRS, strip=True)
        if not title:
            abort(400, "Title required")
        db = get_db()
        db.execute("INSERT INTO questions(title, body, created_at) VALUES(?,?,?)", (title, body, datetime.utcnow()))
        db.commit()
        qid = db.execute("SELECT last_insert_rowid()").fetchone()[0]
        return redirect(url_for("question", qid=qid))
    body = render_template_string(ASK)
    return render_template_string(BASE, body=body)

@app.route("/q/<int:qid>")
def question(qid):
    db = get_db()
    q = db.execute("SELECT * FROM questions WHERE id=?", (qid,)).fetchone()
    if not q:
        abort(404)
    answers = db.execute("SELECT * FROM answers WHERE question_id=?", (qid,)).fetchall()
    body = render_template_string(QUESTION, q=q, answers=answers)
    return render_template_string(BASE, body=body)

@app.route("/q/<int:qid>/answer", methods=["POST"])
def answer(qid):
    db = get_db()
    if not db.execute("SELECT 1 FROM questions WHERE id=?", (qid,)).fetchone():
        abort(404)
    name = (request.form.get("name") or "").strip()
    raw_body = (request.form.get("body") or "").strip()
    body = bleach.clean(raw_body, tags=ALLOWED_TAGS, attributes=ALLOWED_ATTRS, strip=True)
    if not body:
        abort(400, "Body required")
    db.execute(
        "INSERT INTO answers(question_id, body, name, created_at) VALUES(?,?,?,?)",
        (qid, body, name, datetime.utcnow()),
    )
    db.commit()
    return redirect(url_for("question", qid=qid))

@app.route("/suggest", methods=["GET", "POST"])
def suggest():
    db = get_db()
    if request.method == "POST":
        raw_body = (request.form.get("body") or "").strip()
        contact = (request.form.get("contact") or "").strip()
        body = bleach.clean(raw_body, tags=ALLOWED_TAGS, attributes=ALLOWED_ATTRS, strip=True)
        if not body:
            abort(400, "Suggestion text required")
        db.execute("INSERT INTO suggestions(body, contact, created_at) VALUES(?,?,?)",
                   (body, contact, datetime.utcnow()))
        db.commit()
        return redirect(url_for("index"))

    body_html = """
    <div class="bg-white p-5 rounded-2xl shadow-sm">
      <h1 class="text-2xl font-bold mb-3">Send a Suggestion</h1>
      <form method="post" class="space-y-3">
        <div>
          <label class="block text-sm text-zinc-600">Suggestion <span class="text-red-600">*</span></label>
          <input type="hidden" name="body" id="s-body">
          <div id="s-editor" class="bg-white rounded-xl border border-zinc-200"></div>
        </div>
        <div>
          <label class="block text-sm text-zinc-600">Contact (optional)</label>
          <input name="contact" class="w-full px-3 py-2 rounded-xl border border-zinc-200" />
        </div>
        <button class="px-3 py-2 rounded-xl bg-zinc-900 text-white">Submit</button>
      </form>
      <script>
        (function () {
          var sForm = document.currentScript.closest('form');
          var sEditor = new Quill('#s-editor', {
            theme: 'snow',
            placeholder: 'Share your feedback or suggestions…',
            modules: {
              toolbar: [
                [{'header': [1, 2, 3, false]}],
                ['bold', 'italic', 'underline'],
                [{'list': 'ordered'}, {'list': 'bullet'}],
                ['blockquote', 'clean']
              ]
            }
          });
          sForm.addEventListener('submit', function () {
            document.getElementById('s-body').value = sEditor.root.innerHTML;
          });
        })();
      </script>
    </div>
    """
    return render_template_string(BASE, body=body_html)

@app.route("/robots.txt")
def robots():
    return f"User-agent: *\nDisallow: {ADMIN_PATH}\n", 200, {"Content-Type": "text/plain"}

from admin_app import admin_bp
app.register_blueprint(admin_bp, url_prefix=ADMIN_PATH)

if __name__ == "__main__":
    os.makedirs(os.path.dirname(DB_PATH) or ".", exist_ok=True)
    with app.app_context():
        init_db()
    app.run(debug=True, host="0.0.0.0", port=int(os.environ.get("PORT", 5001)))