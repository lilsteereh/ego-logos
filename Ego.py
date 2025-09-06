import os
import sqlite3
from datetime import datetime
from flask import Flask, g, render_template_string, request, redirect, url_for, abort

# --- Config ---
DB_PATH = os.environ.get("QA_DB_PATH", "qa.sqlite3")
SECRET = os.environ.get("FLASK_SECRET", os.urandom(24))

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
        """
    )
    db.commit()

@app.before_request
def ensure_db():
    init_db()

# --- Templates (Tailwind via CDN) ---
BASE = """
<!doctype html>
<html lang=\"en\">
  <head>
    <meta charset=\"utf-8\">
    <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">
    <title>Debate</title>
    <link rel=\"icon\" type=\"image/png\" href=\"/static/e.png\">
    <script src=\"https://cdn.tailwindcss.com\"></script>
  </head>
  <body class=\"bg-zinc-50 text-zinc-900\">
    <div class=\"max-w-3xl mx-auto p-4\">
      <header class=\"flex items-center justify-between mb-6\">
        <a href=\"{{ url_for('index') }}\" class=\"text-2xl font-bold\">Debate</a>
        <a href=\"{{ url_for('ask') }}\" class=\"inline-flex items-center px-3 py-2 rounded-xl bg-zinc-900 text-white hover:bg-zinc-800\">Ask a question</a>
      </header>
      {{ body|safe }}
      <footer class=\"mt-12 text-sm text-zinc-500\">
        <p>Anonymous by default. Add your name only if you want.</p>
      </footer>
    </div>
  </body>
</html>
"""

INDEX = """
<div class=\"space-y-4\">
  <div class=\"bg-white p-4 rounded-2xl shadow-sm\">
    <form method=\"post\" action=\"{{ url_for('quick_ask') }}\" class=\"space-y-2\">
      <label class=\"block text-sm text-zinc-600\">Quick ask</label>
      <input name=\"title\" required maxlength=\"180\" placeholder=\"Ask a concise question...\" class=\"w-full px-3 py-2 rounded-xl border border-zinc-200 focus:outline-none focus:ring-2 focus:ring-zinc-300\" />
      <div class=\"flex gap-2\">
        <button class=\"px-3 py-2 rounded-xl bg-zinc-900 text-white\">Post</button>
        <a href=\"{{ url_for('ask') }}\" class=\"px-3 py-2 rounded-xl border border-zinc-200\">Open full form</a>
      </div>
    </form>
  </div>

  {% for q in questions %}
    <a href=\"{{ url_for('question', qid=q['id']) }}\" class=\"block bg-white p-4 rounded-2xl shadow-sm hover:shadow-md transition\">
      <h2 class=\"text-lg font-semibold\">{{ q['title'] }}</h2>
      {% if q['body'] %}
        <p class=\"text-sm text-zinc-600 mt-1\">{{ q['body'][:180] }}{% if q['body']|length > 180 %}…{% endif %}</p>
      {% endif %}
      <div class=\"text-xs text-zinc-500 mt-2\">{{ q['created_at'] }}</div>
    </a>
  {% else %}
    <p class=\"text-zinc-600\">No questions yet. Be the first to ask.</p>
  {% endfor %}
</div>
"""

ASK = """
<div class=\"bg-white p-4 rounded-2xl shadow-sm\">
  <form method=\"post\" class=\"space-y-3\">
    <div>
      <label class=\"block text-sm text-zinc-600\">Title <span class=\"text-red-600\">*</span></label>
      <input name=\"title\" required maxlength=\"180\" class=\"w-full px-3 py-2 rounded-xl border border-zinc-200 focus:outline-none focus:ring-2 focus:ring-zinc-300\" />
    </div>
    <div>
      <label class=\"block text-sm text-zinc-600\">Details (optional)</label>
      <textarea name=\"body\" rows=\"6\" class=\"w-full px-3 py-2 rounded-xl border border-zinc-200 focus:outline-none focus:ring-2 focus:ring-zinc-300\" placeholder=\"Add context, examples, or constraints…\"></textarea>
    </div>
    <button class=\"px-3 py-2 rounded-xl bg-zinc-900 text-white\">Post question</button>
  </form>
</div>
"""

QUESTION = """
<article class=\"bg-white p-5 rounded-2xl shadow-sm\">
  <h1 class=\"text-2xl font-bold\">{{ q['title'] }}</h1>
  {% if q['body'] %}
    <div class=\"prose prose-zinc max-w-none mt-2\">{{ q['body'] | e | replace('\\n', '<br>') | safe }}</div>
  {% endif %}
  <div class=\"text-xs text-zinc-500 mt-2\">Asked {{ q['created_at'] }}</div>
</article>

<section class=\"mt-6\">
  <h2 class=\"text-lg font-semibold mb-2\">Answers ({{ answers|length }})</h2>
  <div class=\"space-y-3\">
    {% for a in answers %}
      <div class=\"bg-white p-4 rounded-2xl shadow-sm\">
        <div class=\"text-sm text-zinc-600\">by {{ a['name'] or 'Anonymous' }}</div>
        <div class=\"mt-1\">{{ a['body'] | e | replace('\\n', '<br>') | safe }}</div>
        <div class=\"text-xs text-zinc-500 mt-2\">{{ a['created_at'] }}</div>
      </div>
    {% else %}
      <p class=\"text-zinc-600\">No answers yet. Be the first.</p>
    {% endfor %}
  </div>
</section>

<section class=\"mt-6 bg-white p-4 rounded-2xl shadow-sm\">
  <h3 class=\"font-semibold\">Add your answer</h3>
  <form method=\"post\" action=\"{{ url_for('answer', qid=q['id']) }}\" class=\"space-y-3\">
    <div>
      <label class=\"block text-sm text-zinc-600\">Display name (optional)</label>
      <input name=\"name\" maxlength=\"80\" placeholder=\"Leave blank to stay Anonymous\" class=\"w-full px-3 py-2 rounded-xl border border-zinc-200 focus:outline-none focus:ring-2 focus:ring-zinc-300\" />
    </div>
    <div>
      <label class=\"block text-sm text-zinc-600\">Your answer <span class=\"text-red-600\">*</span></label>
      <textarea name=\"body\" rows=\"6\" required class=\"w-full px-3 py-2 rounded-xl border border-zinc-200 focus:outline-none focus:ring-2 focus:ring-zinc-300\"></textarea>
    </div>
    <button class=\"px-3 py-2 rounded-xl bg-zinc-900 text-white\">Post answer</button>
  </form>
</section>
"""

# --- Routes ---
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
    body = render_template_string(QUESTION, q=q, answers=answers)
    return render_template_string(BASE, body=body, title="Debate")

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

# --- Entry ---
if __name__ == "__main__":
    os.makedirs(os.path.dirname(DB_PATH) or ".", exist_ok=True)
    with app.app_context():
        init_db()
    app.run(debug=True, host="0.0.0.0", port=int(os.environ.get("PORT", 5001)))