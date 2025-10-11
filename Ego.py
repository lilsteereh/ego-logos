import os
import sqlite3
from datetime import datetime
import uuid, hashlib, hmac
from flask import Flask, g, render_template_string, request, redirect, url_for, abort, session, send_from_directory, jsonify
import bleach

# --- Config ---
DB_PATH = os.environ.get("QA_DB_PATH", "/var/data/qa.sqlite3")
RAW_SECRET = os.environ.get("FLASK_SECRET")
SECRET = RAW_SECRET.encode("utf-8") if isinstance(RAW_SECRET, str) else (RAW_SECRET or os.urandom(24))

ADMIN_PATH = os.environ.get("ADMIN_PATH", "/__debate-admin-92f1c3")
UPLOAD_DIR = "/var/data/uploads"

app = Flask(__name__)
app.config["SECRET_KEY"] = SECRET

# Allowed HTML tags/attributes for rich text (Quill output)
ALLOWED_TAGS = [
    "b", "i", "u", "em", "strong",
    "p", "br", "h1", "h2", "h3", "blockquote",
    "ul", "ol", "li", "span", "img", "div"
]
ALLOWED_ATTRS = {
    "span": ["style"],
    "div": ["style"],
    "p": ["style"],
    "img": ["src", "alt", "width", "height", "style"]
}

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

        -- Answer votes: one per question per anon device; toggleable; moving between answers allowed
        CREATE TABLE IF NOT EXISTS avotes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            question_id INTEGER NOT NULL,
            answer_id INTEGER NOT NULL,
            anon_hash TEXT NOT NULL,
            ip_hash TEXT,
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(question_id, anon_hash)
        );
        CREATE INDEX IF NOT EXISTS idx_avotes_answer ON avotes(answer_id);
        CREATE INDEX IF NOT EXISTS idx_avotes_question ON avotes(question_id);
        CREATE INDEX IF NOT EXISTS idx_avotes_q_ip ON avotes(question_id, ip_hash);

        -- Question votes: one per question per anon device; toggleable
        CREATE TABLE IF NOT EXISTS qvotes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            question_id INTEGER NOT NULL,
            anon_hash TEXT NOT NULL,
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(question_id, anon_hash)
        );

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
      .prose img { max-width: 100%; height: auto; }
      /* Make inserted images in Quill editors resizable and responsive */
      .ql-editor img {
        max-width: none;
        height: auto;
        resize: both;
        overflow: hidden;
        display: block;
        cursor: nwse-resize;
        border: 1px dashed transparent;
        transition: border-color 0.15s;
      }
      .ql-editor img:hover { border: 1px dashed #d1d5db; }
      /* Vote triangle sizing */
      .vote-tri { font-size: 0.9rem; line-height: 1; }
      @media (max-width: 640px) { .vote-tri { font-size: 0.95rem; } }
      .pressed { transform: translateY(1px); }
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

    <script>
      // Generic helper for vote click -> POST -> update
      async function sendVote(btn, url) {
        if (btn.dataset.loading === '1') return;
        btn.dataset.loading = '1';
        btn.classList.add('pressed');

        try {
          const res = await fetch(url, { method: 'POST', headers: { 'X-Requested-With': 'fetch' }});
          const data = await res.json();
          // Update count & state only after backend confirms
          if (data && data.ok) {
            const countEl = document.getElementById(btn.dataset.countId);
            if (countEl) countEl.textContent = data.count;
            // toggle active color
            if (data.voted) {
              btn.classList.remove('text-zinc-400');
              btn.classList.add('text-amber-500');
              btn.setAttribute('aria-pressed', 'true');
            } else {
              btn.classList.remove('text-amber-500');
              btn.classList.add('text-zinc-400');
              btn.setAttribute('aria-pressed', 'false');
            }

            // For "one answer per question": backend might return a moved_id to un-highlight old
            if (data.cleared_answer_id) {
              const prevBtn = document.querySelector(`[data-aid="${data.cleared_answer_id}"][data-kind="answer"]`);
              if (prevBtn) {
                prevBtn.classList.remove('text-amber-500');
                prevBtn.classList.add('text-zinc-400');
                prevBtn.setAttribute('aria-pressed', 'false');
                const prevCount = document.getElementById(prevBtn.dataset.countId);
                if (prevCount && typeof data.prev_count === 'number') prevCount.textContent = data.prev_count;
              }
            }
          } else {
            console.warn('Vote failed', data);
          }
        } catch (e) {
          console.error(e);
        } finally {
          btn.dataset.loading = '0';
          btn.classList.remove('pressed');
        }
      }
    </script>
  </body>
</html>
"""

# Shared JS helpers injected where needed (toolbar + drag/drop + paste)
QUILL_IMAGE_HELPERS = """
<script>
  function uploadImageFile(file, quill) {
    const formData = new FormData();
    formData.append('file', file);
    return fetch('/upload_image', { method: 'POST', body: formData })
      .then(r => r.json())
      .then(data => {
        if (data && data.url) {
          const range = quill.getSelection(true);
          quill.insertEmbed(range.index, 'image', data.url, 'user');
          quill.setSelection(range.index + 1);
        } else {
          alert('Image upload failed.');
        }
      })
      .catch(() => alert('Image upload failed.'));
  }

  function attachImageHandlers(quill) {
    // Toolbar image button
    const toolbar = quill.getModule('toolbar');
    if (toolbar) {
      toolbar.addHandler('image', () => {
        const input = document.createElement('input');
        input.setAttribute('type', 'file');
        input.setAttribute('accept', 'image/*');
        input.onchange = () => {
          const file = input.files && input.files[0];
          if (file) uploadImageFile(file, quill);
        };
        input.click();
      });
    }

    // Drag & drop
    quill.root.addEventListener('drop', function(e) {
      e.preventDefault();
      if (e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files.length) {
        const file = e.dataTransfer.files[0];
        if (file && file.type.startsWith('image/')) {
          uploadImageFile(file, quill);
        }
      }
    });

    // Paste images from clipboard
    quill.root.addEventListener('paste', function(e) {
      const items = (e.clipboardData || window.clipboardData).items;
      if (!items) return;
      for (let i = 0; i < items.length; i++) {
        const it = items[i];
        if (it.kind === 'file') {
          const file = it.getAsFile();
          if (file && file.type && file.type.startsWith('image/')) {
            e.preventDefault();
            uploadImageFile(file, quill);
            break;
          }
        }
      }
    });
  }
</script>
"""

INDEX = """
<div class="flex items-center justify-between mb-4">
  <h1 class="text-xl font-semibold">Questions</h1>
  <form method="get" class="flex items-center gap-2 text-sm">
    <label class="text-zinc-600">Sort by:</label>
    <select name="sort" onchange="this.form.submit()" class="border border-zinc-200 rounded-xl shadow-sm px-3 py-2 bg-white text-zinc-800 focus:outline-none focus:ring-2 focus:ring-zinc-300 hover:bg-zinc-100 active:bg-zinc-100 transition-all">
      <option value="" {% if sort == '' %}selected{% endif %}>Latest activity</option>
      <option value="recent" {% if sort == 'recent' %}selected{% endif %}>Recently posted</option>
      <option value="bumped" {% if sort == 'bumped' %}selected{% endif %}>Bumped</option>
      <option value="top_day" {% if sort == 'top_day' %}selected{% endif %}>Top 24h</option>
      <option value="top_week" {% if sort == 'top_week' %}selected{% endif %}>Top 7d</option>
      <option value="top_month" {% if sort == 'top_month' %}selected{% endif %}>Top 30d</option>
    </select>
  </form>
</div>

<div class="space-y-4">
  {% for q in questions %}
    <div class="bg-white p-4 rounded-2xl shadow-sm hover:shadow-md transition">
      <div class="flex items-start">
        <div class="flex-1">
          <a href="{{ url_for('question', qid=q['id']) }}">
            <h2 class="text-lg font-semibold">{{ q['title'] }}</h2>
          </a>
          {% if q['body'] %}
            {% set preview = (q['body'] | striptags) %}
            <p class="text-sm text-zinc-600 mt-1">{{ preview[:180] }}{% if preview|length > 180 %}…{% endif %}</p>
          {% endif %}
          <div class="text-xs text-zinc-500 mt-2">{{ q['created_at'] }}</div>
        </div>
        <div class="pl-3 text-center">
          {% set qv_count_id = 'qv-count-' ~ q['id'] %}
          <button
            type="button"
            class="vote-tri transition text-{{ 'amber-500' if q['voted'] else 'zinc-400' }}"
            aria-pressed="{{ 'true' if q['voted'] else 'false' }}"
            data-kind="question"
            data-qid="{{ q['id'] }}"
            data-count-id="{{ qv_count_id }}"
            onclick="sendVote(this, '{{ url_for('vote_question', qid=q['id']) }}')"
          >▲</button>
          <div id="{{ qv_count_id }}" class="text-xs mt-1">{{ q['qvotes'] or 0 }}</div>
        </div>
      </div>
    </div>
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

  {{ quill_helpers|safe }}
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
            ['blockquote', 'image', 'clean']
          ]
        }
      });

      attachImageHandlers(qEditor);

      var form = document.getElementById('ask-form');
      form.addEventListener('submit', function () {
        var html = qEditor.root.innerHTML.trim();
        document.getElementById('q-body').value = (html === '<p><br></p>' ? '' : html);
      });
    });
  </script>
</div>
"""

QUESTION = """
<article class="bg-white p-5 rounded-2xl shadow-sm">
  <div class="flex items-start">
    <div class="flex-1">
      <h1 class="text-2xl font-bold">{{ q['title'] }}</h1>
      {% if q['body'] %}
        <div class="prose prose-zinc max-w-none mt-2">{{ q['body'] | safe }}</div>
      {% endif %}
      <div class="text-xs text-zinc-500 mt-2">Asked {{ q['created_at'] }}</div>
    </div>
    <div class="pl-3 text-center">
      {% set qv_count_id = 'qv-count-' ~ q['id'] %}
      <button
        type="button"
        class="vote-tri transition text-{{ 'amber-500' if qv_voted else 'zinc-400' }}"
        aria-pressed="{{ 'true' if qv_voted else 'false' }}"
        data-kind="question"
        data-qid="{{ q['id'] }}"
        data-count-id="{{ qv_count_id }}"
        onclick="sendVote(this, '{{ url_for('vote_question', qid=q['id']) }}')"
      >▲</button>
      <div id="{{ qv_count_id }}" class="text-xs mt-1">{{ qv_count or 0 }}</div>
    </div>
  </div>
</article>

<section class="mt-6">
  <h2 class="text-lg font-semibold mb-2">Answers ({{ answers|length }})</h2>
  <div class="space-y-3">
    {% for a in answers %}
      <div class="bg-white p-4 rounded-2xl shadow-sm">
        <div class="flex items-start">
          <div class="flex-1">
            <div class="text-sm text-zinc-600">by {{ a['name'] or 'Anonymous' }}</div>
            <div class="prose prose-zinc max-w-none mt-1">{{ a['body'] | safe }}</div>
            <div class="text-xs text-zinc-500 mt-2">{{ a['created_at'] }}</div>
          </div>
          <div class="pl-3 text-center">
            {% set aid = a['id'] %}
            {% set count_id = 'av-count-' ~ aid %}
            {% set voted = (current_answer_id == aid) %}
            <button
              type="button"
              class="vote-tri transition text-{{ 'amber-500' if voted else 'zinc-400' }}"
              aria-pressed="{{ 'true' if voted else 'false' }}"
              data-kind="answer"
              data-qid="{{ q['id'] }}"
              data-aid="{{ aid }}"
              data-count-id="{{ count_id }}"
              onclick="sendVote(this, '{{ url_for('vote_answer', qid=q['id'], aid=aid) }}')"
            >▲</button>
            <div id="{{ count_id }}" class="text-xs mt-1">{{ vote_counts.get(aid, 0) }}</div>
          </div>
        </div>
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

  {{ quill_helpers|safe }}
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
            ['blockquote', 'image', 'clean']
          ]
        }
      });

      attachImageHandlers(aEditor);

      var form = document.getElementById('answer-form');
      form.addEventListener('submit', function () {
        var html = aEditor.root.innerHTML.trim();
        document.getElementById('a-body').value = (html === '<p><br></p>' ? '' : html);
      });
    });
  </script>
</section>
"""

SUGGEST_FORM = """
<div class="bg-white p-5 rounded-2xl shadow-sm">
  <h1 class="text-2xl font-bold mb-3">Send a Suggestion</h1>
  <form id="s-form" method="post" class="space-y-3">
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

  {{ quill_helpers|safe }}
  <script>
    document.addEventListener('DOMContentLoaded', function () {
      var sEditor = new Quill('#s-editor', {
        theme: 'snow',
        placeholder: 'Share your feedback or suggestions…',
        modules: {
          toolbar: [
            [{ 'header': [1, 2, 3, false] }],
            ['bold', 'italic', 'underline'],
            [{ 'list': 'ordered' }, { 'list': 'bullet' }],
            ['blockquote', 'image', 'clean']
          ]
        }
      });

      attachImageHandlers(sEditor);

      var sForm = document.getElementById('s-form');
      sForm.addEventListener('submit', function () {
        var html = sEditor.root.innerHTML.trim();
        document.getElementById('s-body').value = (html === '<p><br></p>' ? '' : html);
      });
    });
  </script>
</div>
"""

# --- Routes ---
@app.route("/")
def index():
    sort = request.args.get("sort", "").strip()
    db = get_db()

    # For each question, we also want current user's question-voted state and total qvotes
    # Build base lists differently per sort
    if sort in ("", "bumped"):
        qs = db.execute("""
            SELECT q.id, q.title, q.body, q.created_at,
                   MAX(COALESCE(a.created_at, q.created_at)) AS activity_time
            FROM questions q
            LEFT JOIN answers a ON a.question_id = q.id
            GROUP BY q.id
            ORDER BY activity_time DESC
            LIMIT 50
        """).fetchall()

    elif sort == "recent":
        qs = db.execute("""
            SELECT id, title, body, created_at
            FROM questions
            ORDER BY created_at DESC
            LIMIT 50
        """).fetchall()

    elif sort in ("top_day", "top_week", "top_month"):
        days = {"top_day": 1, "top_week": 7, "top_month": 30}[sort]
        # Combine qvotes and avotes in timeframe
        qs = db.execute(f"""
            SELECT q.id, q.title, q.body, q.created_at,
                   COALESCE(qv.cnt, 0) + COALESCE(av.cnt, 0) AS votes
            FROM questions q
            LEFT JOIN (
                SELECT question_id, COUNT(*) AS cnt
                FROM qvotes
                WHERE created_at >= datetime('now', '-{days} day')
                GROUP BY question_id
            ) qv ON qv.question_id = q.id
            LEFT JOIN (
                SELECT a.question_id, COUNT(*) AS cnt
                FROM avotes v
                JOIN answers a ON a.id = v.answer_id
                WHERE v.created_at >= datetime('now', '-{days} day')
                GROUP BY a.question_id
            ) av ON av.question_id = q.id
            ORDER BY votes DESC, q.created_at DESC
            LIMIT 50
        """).fetchall()
    else:
        qs = db.execute("""
            SELECT id, title, body, created_at
            FROM questions
            ORDER BY created_at DESC
            LIMIT 50
        """).fetchall()

    # enrich with vote counts and current user's state
    anon_hash = make_anon_hash(session.get('anon_id') or "")
    q_ids = [row['id'] for row in qs]
    qv_counts = {}
    qv_voted = set()
    if q_ids:
        placeholders = ",".join("?" * len(q_ids))
        rows = db.execute(f"SELECT question_id, COUNT(*) c FROM qvotes WHERE question_id IN ({placeholders}) GROUP BY question_id", q_ids).fetchall()
        qv_counts = {r['question_id']: r['c'] for r in rows}
        rows = db.execute(f"SELECT question_id FROM qvotes WHERE anon_hash=? AND question_id IN ({placeholders})", (anon_hash, *q_ids)).fetchall()
        qv_voted = {r['question_id'] for r in rows}

    # convert to dicts with extra fields
    enriched = []
    for row in qs:
        d = dict(row)
        d['qvotes'] = qv_counts.get(row['id'], 0)
        d['voted'] = (row['id'] in qv_voted)
        enriched.append(d)

    body = render_template_string(INDEX, questions=enriched, sort=sort)
    return render_template_string(BASE, body=body, quill_helpers=QUILL_IMAGE_HELPERS)

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
    body = render_template_string(ASK, quill_helpers=QUILL_IMAGE_HELPERS)
    return render_template_string(BASE, body=body, quill_helpers=QUILL_IMAGE_HELPERS)

@app.route("/q/<int:qid>")
def question(qid):
    db = get_db()
    q = db.execute("SELECT * FROM questions WHERE id=?", (qid,)).fetchone()
    if not q:
        abort(404)
    answers = db.execute("SELECT * FROM answers WHERE question_id=?", (qid,)).fetchall()

    # counts for answers
    rows = db.execute("SELECT answer_id, COUNT(*) AS c FROM avotes WHERE question_id=? GROUP BY answer_id", (qid,)).fetchall()
    vote_counts = {r['answer_id']: r['c'] for r in rows}

    # current device
    anon_hash = make_anon_hash(session.get('anon_id') or "")
    row = db.execute("SELECT answer_id FROM avotes WHERE question_id=? AND anon_hash=?", (qid, anon_hash)).fetchone()
    current_answer_id = row['answer_id'] if row else None

    # question vote state
    qv_count = db.execute("SELECT COUNT(*) FROM qvotes WHERE question_id=?", (qid,)).fetchone()[0]
    qv_voted = db.execute("SELECT 1 FROM qvotes WHERE question_id=? AND anon_hash=?", (qid, anon_hash)).fetchone() is not None

    body = render_template_string(QUESTION, q=q, answers=answers, vote_counts=vote_counts, current_answer_id=current_answer_id, qv_count=qv_count, qv_voted=qv_voted, quill_helpers=QUILL_IMAGE_HELPERS)
    return render_template_string(BASE, body=body, quill_helpers=QUILL_IMAGE_HELPERS)

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

# --- AJAX Voting ---

@app.route("/q/<int:qid>/vote-question", methods=["POST"])
def vote_question(qid):
    db = get_db()
    if not db.execute("SELECT 1 FROM questions WHERE id=?", (qid,)).fetchone():
        return jsonify(ok=False, error="not_found"), 404

    anon_hash = make_anon_hash(session.get('anon_id') or "")

    existing = db.execute("SELECT id FROM qvotes WHERE question_id=? AND anon_hash=?", (qid, anon_hash)).fetchone()
    if existing:
        db.execute("DELETE FROM qvotes WHERE id=?", (existing['id'],))
        db.commit()
        voted = False
    else:
        db.execute("INSERT INTO qvotes(question_id, anon_hash, created_at) VALUES(?,?,?)", (qid, anon_hash, datetime.utcnow()))
        db.commit()
        voted = True

    count = db.execute("SELECT COUNT(*) FROM qvotes WHERE question_id=?", (qid,)).fetchone()[0]
    return jsonify(ok=True, voted=voted, count=count)

@app.route("/q/<int:qid>/answer/<int:aid>/vote", methods=["POST"])
def vote_answer(qid, aid):
    db = get_db()
    if not db.execute("SELECT 1 FROM answers WHERE id=? AND question_id=?", (aid, qid)).fetchone():
        return jsonify(ok=False, error="not_found"), 404

    anon_hash = make_anon_hash(session.get('anon_id') or "")
    ip_hash = make_ip_hash(client_ip())

    # SOFT CAP: if any other anon from this /24 voted in the last day on this question, show banner (but since this is AJAX, just refuse)
    recent_other = db.execute("""
        SELECT 1 FROM avotes
        WHERE question_id=? AND ip_hash=? AND anon_hash<>? AND created_at >= datetime('now','-1 day')
        LIMIT 1
    """, (qid, ip_hash, anon_hash)).fetchone()
    if recent_other:
        # Refuse without changing UI; client keeps button state.
        return jsonify(ok=False, error="ip_cap"), 429

    existing = db.execute("SELECT id, answer_id FROM avotes WHERE question_id=? AND anon_hash=?", (qid, anon_hash)).fetchone()

    cleared_answer_id = None
    prev_count = None

    if existing:
        if existing['answer_id'] == aid:
            # toggle off
            db.execute("DELETE FROM avotes WHERE id=?", (existing['id'],))
            db.commit()
            voted = False
        else:
            # move vote to another answer
            cleared_answer_id = existing['answer_id']
            # old count after removal
            db.execute("UPDATE avotes SET answer_id=?, ip_hash=?, created_at=? WHERE id=?", (aid, ip_hash, datetime.utcnow(), existing['id']))
            db.commit()
            voted = True
            # recompute previous answer's count for UI correction
            prev_count = db.execute("SELECT COUNT(*) FROM avotes WHERE question_id=? AND answer_id=?", (qid, cleared_answer_id)).fetchone()[0]
    else:
        db.execute("INSERT INTO avotes(question_id, answer_id, anon_hash, ip_hash, created_at) VALUES(?,?,?,?,?)", (qid, aid, anon_hash, ip_hash, datetime.utcnow()))
        db.commit()
        voted = True

    count = db.execute("SELECT COUNT(*) FROM avotes WHERE question_id=? AND answer_id=?", (qid, aid)).fetchone()[0]
    return jsonify(ok=True, voted=voted, count=count, cleared_answer_id=cleared_answer_id, prev_count=prev_count)

# --- Suggestions ---

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

    body_html = render_template_string(SUGGEST_FORM, quill_helpers=QUILL_IMAGE_HELPERS)
    return render_template_string(BASE, body=body_html, quill_helpers=QUILL_IMAGE_HELPERS)

@app.route("/robots.txt")
def robots():
    return f"User-agent: *\nDisallow: {ADMIN_PATH}\n", 200, {"Content-Type": "text/plain"}

from admin_app import admin_bp
app.register_blueprint(admin_bp, url_prefix=ADMIN_PATH)

# --- Image Uploads ---
@app.route("/upload_image", methods=["POST"])
def upload_image():
    file = request.files.get("file")
    if not file:
        return {"error": "No file provided"}, 400

    # Validate extension
    allowed = {"png", "jpg", "jpeg", "gif", "webp"}
    if "." not in file.filename:
        return {"error": "Invalid filename"}, 400
    ext = file.filename.rsplit(".", 1)[-1].lower()
    if ext not in allowed:
        return {"error": "Invalid file type"}, 400

    os.makedirs(UPLOAD_DIR, exist_ok=True)
    filename = f"{uuid.uuid4().hex}.{ext}"
    path = os.path.join(UPLOAD_DIR, filename)
    file.save(path)

    return {"url": f"/uploads/{filename}"}

@app.route("/uploads/<path:filename>")
def serve_upload(filename):
    return send_from_directory(UPLOAD_DIR, filename)

# --- Entry ---
if __name__ == "__main__":
    os.makedirs(os.path.dirname(DB_PATH) or ".", exist_ok=True)
    os.makedirs(UPLOAD_DIR, exist_ok=True)
    with app.app_context():
        init_db()
    app.run(debug=True, host="0.0.0.0", port=int(os.environ.get("PORT", 5001)))