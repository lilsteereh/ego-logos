import os
import sqlite3
from datetime import datetime
import uuid, hashlib, hmac
from flask import Flask, g, render_template_string, request, redirect, url_for, abort, session

# --- Config ---
DB_PATH = os.environ.get("QA_DB_PATH", "/var/data/qa.sqlite3")
RAW_SECRET = os.environ.get("FLASK_SECRET")
SECRET = RAW_SECRET.encode("utf-8") if isinstance(RAW_SECRET, str) else (RAW_SECRET or os.urandom(24))

# Secret admin path (change via env on Render if desired)
ADMIN_PATH = os.environ.get("ADMIN_PATH", "/__debate-admin-92f1c3")

app = Flask(__name__)
app.config["SECRET_KEY"] = SECRET

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

        -- one vote per question per anon device (cookie-hash)
        CREATE TABLE IF NOT EXISTS votes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            question_id INTEGER NOT NULL,
            answer_id INTEGER NOT NULL,
            anon_hash TEXT NOT NULL,
            ip_hash TEXT, -- nullable; soft per-IP cap enforced in app logic
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(question_id, anon_hash)
        );
        CREATE INDEX IF NOT EXISTS idx_votes_answer ON votes(answer_id);
        CREATE INDEX IF NOT EXISTS idx_votes_question ON votes(question_id);
        CREATE INDEX IF NOT EXISTS idx_votes_q_ip ON votes(question_id, ip_hash);

        -- suggestions sent by users
        CREATE TABLE IF NOT EXISTS suggestions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            body TEXT NOT NULL,
            contact TEXT,
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE INDEX IF NOT EXISTS idx_suggestions_created ON suggestions(created_at);
        """
    )
    db.commit()
    # Backfill ip_hash column if DB was created before
    try:
        db.execute("ALTER TABLE votes ADD COLUMN ip_hash TEXT")
        db.commit()
    except sqlite3.OperationalError:
        pass

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
    # Soft per-IP: IPv4 /24, IPv6 /64
    if ":" in ip:
        parts = ip.split(":")
        net = ":".join(parts[:4])
    else:
        parts = ip.split(".")
        net = ".".join(parts[:3] + ["0"]) if len(parts) == 4 else ip
    return hmac.new(SECRET, net.encode("utf-8"), hashlib.sha256).hexdigest()

# --- Templates (Tailwind via CDN) ---
BASE = """
<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Debate</title>
    <link rel="icon" type="image/png" href="/static/e.png">
    <script src="https://cdn.tailwindcss.com"></script>
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
      <footer class="mt-12 text-sm text-zinc-500">
        <p>Anonymous by default. Add your name only if you want.</p>
      </footer>
    </div>
  </body>
</html>
"""

INDEX = """
<div class="space-y-4">
  <div class="bg-white p-4 rounded-2xl shadow-sm">
    <form method="post" action="{{ url_for('quick_ask') }}" class="space-y-2">
      <label class="block text-sm text-zinc-600">Quick ask</label>
      <input name="title" required maxlength="180" placeholder="Ask a concise question..." class="w-full px-3 py-2 rounded-xl border border-zinc-200 focus:outline-none focus:ring-2 focus:ring-zinc-300" />
      <div class="flex gap-2">
        <button class="px-3 py-2 rounded-xl bg-zinc-900 text-white">Post</button>
        <a href="{{ url_for('ask') }}" class="px-3 py-2 rounded-xl border border-zinc-200">Open full form</a>
      </div>
    </form>
  </div>

  {% for q in questions %}
    <a href="{{ url_for('question', qid=q['id']) }}" class="block bg-white p-4 rounded-2xl shadow-sm hover:shadow-md transition">
      <h2 class="text-lg font-semibold">{{ q['title'] }}</h2>
      {% if q['body'] %}
        <p class="text-sm text-zinc-600 mt-1">{{ q['body'][:180] }}{% if q['body']|length > 180 %}…{% endif %}</p>
      {% endif %}
      <div class="text-xs text-zinc-500 mt-2">{{ q['created_at'] }}</div>
    </a>
  {% else %}
    <p class="text-zinc-600">No questions yet. Be the first to ask.</p>
  {% endfor %}
</div>
"""

ASK = """
<div class="bg-white p-4 rounded-2xl shadow-sm">
  <form method="post" class="space-y-3">
    <div>
      <label class="block text-sm text-zinc-600">Title <span class="text-red-600">*</span></label>
      <input name="title" required maxlength="180" class="w-full px-3 py-2 rounded-xl border border-zinc-200 focus:outline-none focus:ring-2 focus:ring-zinc-300" />
    </div>
    <div>
      <label class="block text-sm text-zinc-600">Details (optional)</label>
      <textarea name="body" rows="6" class="w-full px-3 py-2 rounded-xl border border-zinc-200 focus:outline-none focus:ring-2 focus:ring-zinc-300" placeholder="Add context, examples, or constraints…"></textarea>
    </div>
    <button class="px-3 py-2 rounded-2xl bg-zinc-900 text-white">Post question</button>
  </form>
</div>
"""

SUGGEST = """
<div class="bg-white p-4 rounded-2xl shadow-sm">
  <h2 class="text-lg font-semibold mb-2">Send a suggestion</h2>
  <form method="post" class="space-y-3">
    <div>
      <label class="block text-sm text-zinc-600">Your suggestion <span class="text-red-600">*</span></label>
      <textarea name="body" rows="6" required class="w-full px-3 py-2 rounded-xl border border-zinc-200 focus:outline-none focus:ring-2 focus:ring-zinc-300" placeholder="What should we add or improve?"></textarea>
    </div>
    <div>
      <label class="block text-sm text-zinc-600">Contact (optional)</label>
      <input name="contact" maxlength="160" class="w-full px-3 py-2 rounded-xl border border-zinc-200 focus:outline-none focus:ring-2 focus:ring-zinc-300" placeholder="Email, X/IG handle, etc. (optional)" />
    </div>
    <button class="px-3 py-2 rounded-xl bg-zinc-900 text-white">Send</button>
  </form>
</div>
"""

QUESTION = """
<article class="bg-white p-5 rounded-2xl shadow-sm">
  <h1 class="text-2xl font-bold">{{ q['title'] }}</h1>
  {% if q['body'] %}
    <div class="prose prose-zinc max-w-none mt-2">{{ q['body'] | e | replace('\\n', '<br>') | safe }}</div>
  {% endif %}
  <div class="text-xs text-zinc-500 mt-2">Asked {{ q['created_at'] }}</div>
</article>

<section class="mt-6">
  <h2 class="text-lg font-semibold mb-2">Answers ({{ answers|length }})</h2>
  {% if request.args.get('cap') %}
    <div class="mb-3 text-sm text-amber-700 bg-amber-50 border border-amber-200 rounded-xl p-3">
      Voting from this network is temporarily limited for this question.
    </div>
  {% endif %}
  <div class="space-y-3">
    {% for a in answers %}
      {% set count = vote_counts.get(a['id'], 0) %}
      {% set picked = (current_answer_id == a['id']) %}
      <div class="bg-white p-4 rounded-2xl shadow-sm">
        <div class="flex items-start justify-between gap-4">
          <div>
            <div class="text-sm text-zinc-600">by {{ a['name'] or 'Anonymous' }}</div>
            <div class="mt-1">{{ a['body'] | e | replace('\\n', '<br>') | safe }}</div>
            <div class="text-xs text-zinc-500 mt-2">{{ a['created_at'] }}</div>
          </div>
          <form method="post" action="{{ url_for('vote', qid=q['id'], aid=a['id']) }}">
            <button class="px-3 py-2 rounded-xl border text-sm transition {{ 'bg-emerald-600 text-white border-emerald-700' if picked else 'border-zinc-300 hover:bg-zinc-50' }}" title="Click to upvote or remove your vote. One per question">
              ▲ {{ count }}
            </button>
          </form>
        </div>
      </div>
    {% else %}
      <p class="text-zinc-600">No answers yet. Be the first.</p>
    {% endfor %}
  </div>
  <p class="text-xs text-zinc-500 mt-2">One vote per question per device (and soft-limited per network). You can change or remove your vote.</p>
</section>

<section class="mt-6 bg-white p-4 rounded-2xl shadow-sm">
  <h3 class="font-semibold">Add your answer</h3>
  <form method="post" action="{{ url_for('answer', qid=q['id']) }}" class="space-y-3">
    <div>
      <label class="block text-sm text-zinc-600">Display name (optional)</label>
      <input name="name" maxlength="80" placeholder="Leave blank to stay Anonymous" class="w-full px-3 py-2 rounded-xl border border-zinc-200 focus:outline-none focus:ring-2 focus:ring-zinc-300" />
    </div>
    <div>
      <label class="block text-sm text-zinc-600">Your answer <span class="text-red-600">*</span></label>
      <textarea name="body" rows="6" required class="w-full px-3 py-2 rounded-xl border border-zinc-200 focus:outline-none focus:ring-2 focus:ring-zinc-300"></textarea>
    </div>
    <button class="px-3 py-2 rounded-xl bg-zinc-900 text-white">Post answer</button>
  </form>
</section>
"""

# --- Robots.txt (hide admin path) ---
@app.route("/robots.txt")
def robots():
    return f"User-agent: *\nDisallow: {ADMIN_PATH}\n", 200, {"Content-Type": "text/plain"}

# --- Public routes ---
@app.route("/")
def index():
    db = get_db()
    cur = db.execute("SELECT id, title, body, created_at FROM questions ORDER BY id DESC LIMIT 50")
    questions = cur.fetchall()
    body = render_template_string(INDEX, questions=questions)
    return render_template_string(BASE, body=body, title="Debate")

@app.route("/ask", methods=["GET", "POST"])
def ask():
    if request.method == "POST":
        title = (request.form.get("title") or "").strip()
        body = (request.form.get("body") or "").strip()
        if not title:
            abort(400, "Title is required")
        db = get_db()
        db.execute("INSERT INTO questions(title, body, created_at) VALUES(?,?,?)", (title, body, datetime.utcnow()))
        db.commit()
        qid = db.execute("SELECT last_insert_rowid()").fetchone()[0]
        return redirect(url_for("question", qid=qid))
    body = render_template_string(ASK)
    return render_template_string(BASE, body=body, title="Debate")

@app.route("/suggest", methods=["GET","POST"])
def suggest():
    if request.method == "POST":
        body = (request.form.get("body") or "").strip()
        contact = (request.form.get("contact") or "").strip()
        if not body:
            abort(400, "Suggestion text is required")
        db = get_db()
        db.execute("INSERT INTO suggestions(body, contact, created_at) VALUES(?,?,?)", (body, contact, datetime.utcnow()))
        db.commit()
        thanks = """
        <div class="bg-white p-6 rounded-2xl shadow-sm text-center">
          <h2 class="text-xl font-semibold mb-2">Thank you!</h2>
          <p class="text-zinc-600">Your suggestion was received.</p>
          <a href="{{ url_for('index') }}" class="inline-block mt-4 px-3 py-2 rounded-xl border border-zinc-300">Back to home</a>
        </div>
        """
        return render_template_string(BASE, body=thanks, title="Thanks")
    body = render_template_string(SUGGEST)
    return render_template_string(BASE, body=body, title="Suggestions")

@app.route("/quick-ask", methods=["POST"])
def quick_ask():
    title = (request.form.get("title") or "").strip()
    if not title:
        abort(400, "Title is required")
    db = get_db()
    db.execute("INSERT INTO questions(title, body, created_at) VALUES(?,?,?)", (title, "", datetime.utcnow()))
    db.commit()
    qid = db.execute("SELECT last_insert_rowid()").fetchone()[0]
    return redirect(url_for("question", qid=qid))

@app.route("/q/<int:qid>")
def question(qid):
    db = get_db()
    q = db.execute("SELECT id, title, body, created_at FROM questions WHERE id=?", (qid,)).fetchone()
    if not q:
        abort(404)
    answers = db.execute("SELECT id, body, name, created_at FROM answers WHERE question_id=? ORDER BY id ASC", (qid,)).fetchall()
    rows = db.execute("SELECT answer_id, COUNT(*) AS c FROM votes WHERE question_id=? GROUP BY answer_id", (qid,)).fetchall()
    vote_counts = {r[0]: r[1] for r in rows}

    anon_id = session.get('anon_id') or ""
    anon_hash = make_anon_hash(anon_id)
    row = db.execute("SELECT answer_id FROM votes WHERE question_id=? AND anon_hash=?", (qid, anon_hash)).fetchone()
    current_answer_id = row[0] if row else None

    body = render_template_string(QUESTION, q=q, answers=answers, vote_counts=vote_counts, current_answer_id=current_answer_id)
    return render_template_string(BASE, body=body, title=q["title"])

@app.route("/q/<int:qid>/answer", methods=["POST"])
def answer(qid):
    db = get_db()
    q = db.execute("SELECT id FROM questions WHERE id=?", (qid,)).fetchone()
    if not q:
        abort(404)
    name = (request.form.get("name") or "").strip()
    body = (request.form.get("body") or "").strip()
    if not body:
        abort(400, "Answer body required")
    db.execute(
        "INSERT INTO answers(question_id, body, name, created_at) VALUES(?,?,?,?)",
        (qid, body, name, datetime.utcnow()),
    )
    db.commit()
    return redirect(url_for("question", qid=qid))

@app.route("/q/<int:qid>/answer/<int:aid>/vote", methods=["POST"])
def vote(qid, aid):
    db = get_db()
    if not db.execute("SELECT 1 FROM questions WHERE id=?", (qid,)).fetchone():
        abort(404)
    if not db.execute("SELECT 1 FROM answers WHERE id=? AND question_id=?", (aid, qid)).fetchone():
        abort(404)

    anon_id = session.get('anon_id')
    if not anon_id:
        session['anon_id'] = uuid.uuid4().hex
        anon_id = session['anon_id']
    anon_hash = make_anon_hash(anon_id)

    ip_hash = make_ip_hash(client_ip())
    recent_other = db.execute(
        """
        SELECT 1 FROM votes
        WHERE question_id=? AND ip_hash=? AND anon_hash<>? AND created_at >= datetime('now','-1 day')
        LIMIT 1
        """,
        (qid, ip_hash, anon_hash),
    ).fetchone()
    if recent_other:
        return redirect(url_for('question', qid=qid, cap=1))

    existing = db.execute(
        "SELECT id, answer_id FROM votes WHERE question_id=? AND anon_hash=?",
        (qid, anon_hash),
    ).fetchone()

    if existing:
        if existing['answer_id'] == aid:
            db.execute("DELETE FROM votes WHERE id=?", (existing['id'],))
            db.commit()
        else:
            db.execute(
                "UPDATE votes SET answer_id=?, ip_hash=?, created_at=? WHERE id=?",
                (aid, ip_hash, datetime.utcnow(), existing['id'])
            )
            db.commit()
    else:
        db.execute(
            "INSERT INTO votes(question_id, answer_id, anon_hash, ip_hash, created_at) VALUES(?,?,?,?,?)",
            (qid, aid, anon_hash, ip_hash, datetime.utcnow())
        )
        db.commit()

    return redirect(url_for('question', qid=qid))

# --- Register Admin blueprint ---
from admin_app import admin_bp
app.register_blueprint(admin_bp, url_prefix=ADMIN_PATH)

# --- Entry ---
if __name__ == "__main__":
    os.makedirs(os.path.dirname(DB_PATH) or ".", exist_ok=True)
    with app.app_context():
        init_db()
    app.run(debug=True, host="0.0.0.0", port=int(os.environ.get("PORT", 5001)))
