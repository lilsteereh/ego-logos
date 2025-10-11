import os
import sqlite3
from flask import Blueprint, render_template_string, request, redirect, url_for, g

# Use the same DB as the main app without importing it (avoids circular import)
DB_PATH = os.environ.get("QA_DB_PATH", "/var/data/qa.sqlite3")

admin_bp = Blueprint("admin", __name__)  # url_prefix is set in Ego.py register_blueprint

# ---------- DB helpers ----------
def get_db():
    if "admin_db" not in g:
        g.admin_db = sqlite3.connect(DB_PATH, detect_types=sqlite3.PARSE_DECLTYPES)
        g.admin_db.row_factory = sqlite3.Row
        ensure_min_tables(g.admin_db)
    return g.admin_db

def ensure_min_tables(db):
    # Make sure analytics exists. (Main app also creates; this is a safe guard.)
    db.executescript("""
        CREATE TABLE IF NOT EXISTS analytics (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_type TEXT NOT NULL,
            path TEXT,
            ip_hash TEXT,
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE INDEX IF NOT EXISTS idx_analytics_event_time
            ON analytics(event_type, created_at);
    """)
    db.commit()

@admin_bp.teardown_app_request
def close_db(exception):
    db = g.pop("admin_db", None)
    if db is not None:
        db.close()

# ---------- Tiny base template ----------
ADMIN_BASE = """
<!doctype html>
<html>
  <head>
    <meta charset="utf-8">
    <title>Admin</title>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <script src="https://cdn.tailwindcss.com"></script>
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
    <style>
      html, body { font-family: ui-sans-serif, system-ui, -apple-system, Segoe UI, Roboto, Helvetica, Arial; }
      .container { max-width: 1100px; margin: 0 auto; padding: 1rem; }
      .card { background: #fff; border-radius: 16px; padding: 1rem; box-shadow: 0 1px 4px rgba(0,0,0,.06); }
      .nav a { margin-right: .75rem; color:#111827; }
      .nav a:hover { text-decoration: underline; }
      table { width: 100%; border-collapse: collapse; }
      th, td { padding: .5rem .75rem; border-bottom: 1px solid #e5e7eb; text-align: left; }
      .mono { font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", "Courier New", monospace; }
    </style>
  </head>
  <body class="bg-zinc-50">
    <div class="container">
      <header class="flex items-center justify-between mb-6">
        <h1 class="text-2xl font-semibold">Admin</h1>
        <nav class="nav">
          <a href="{{ url_for('admin.dashboard') }}">Home</a>
          <a href="{{ url_for('admin.questions') }}">Questions</a>
          <a href="{{ url_for('admin.answers') }}">Answers</a>
          <a href="{{ url_for('admin.analytics') }}">Analytics</a>
        </nav>
      </header>
      {{ body|safe }}
    </div>
  </body>
</html>
"""

# ---------- Routes ----------
@admin_bp.route("/")
def dashboard():
    db = get_db()
    totals = db.execute("""
        SELECT
          (SELECT COUNT(*) FROM questions)       AS questions,
          (SELECT COUNT(*) FROM answers)         AS answers,
          (SELECT COUNT(*) FROM qvotes)          AS qvotes,
          (SELECT COUNT(*) FROM avotes)          AS avotes,
          (SELECT COUNT(*) FROM suggestions)     AS suggestions,
          (SELECT COUNT(*) FROM analytics)       AS events
    """).fetchone()
    body = render_template_string("""
      <div class="grid md:grid-cols-3 gap-4">
        <div class="card"><div class="text-sm text-zinc-500">Questions</div><div class="text-2xl font-bold">{{ t['questions'] }}</div></div>
        <div class="card"><div class="text-sm text-zinc-500">Answers</div><div class="text-2xl font-bold">{{ t['answers'] }}</div></div>
        <div class="card"><div class="text-sm text-zinc-500">Question votes</div><div class="text-2xl font-bold">{{ t['qvotes'] }}</div></div>
        <div class="card"><div class="text-sm text-zinc-500">Answer votes</div><div class="text-2xl font-bold">{{ t['avotes'] }}</div></div>
        <div class="card"><div class="text-sm text-zinc-500">Suggestions</div><div class="text-2xl font-bold">{{ t['suggestions'] }}</div></div>
        <div class="card"><div class="text-sm text-zinc-500">Analytics events</div><div class="text-2xl font-bold">{{ t['events'] }}</div></div>
      </div>
    """, t=totals)
    return render_template_string(ADMIN_BASE, body=body)

@admin_bp.route("/questions")
def questions():
    db = get_db()
    rows = db.execute("""
        SELECT q.id, q.title, q.created_at, COUNT(a.id) AS acount
        FROM questions q
        LEFT JOIN answers a ON a.question_id = q.id
        GROUP BY q.id
        ORDER BY q.created_at DESC
        LIMIT 200
    """).fetchall()
    body = render_template_string("""
      <div class="card">
        <h2 class="text-xl font-semibold mb-3">Questions</h2>
        <table>
          <thead><tr><th>ID</th><th>Title</th><th>Answers</th><th>Created</th><th></th></tr></thead>
          <tbody>
            {% for r in rows %}
              <tr>
                <td class="mono">{{ r['id'] }}</td>
                <td>{{ r['title'] }}</td>
                <td>{{ r['acount'] }}</td>
                <td class="text-zinc-500">{{ r['created_at'] }}</td>
                <td><a class="text-red-600" href="{{ url_for('admin.delete_question', qid=r['id']) }}" onclick="return confirm('Delete question #{{ r['id'] }}?')">Delete</a></td>
              </tr>
            {% endfor %}
          </tbody>
        </table>
      </div>
    """, rows=rows)
    return render_template_string(ADMIN_BASE, body=body)

@admin_bp.route("/delete-question/<int:qid>")
def delete_question(qid):
    db = get_db()
    db.execute("DELETE FROM questions WHERE id=?", (qid,))
    db.commit()
    return redirect(url_for("admin.questions"))

@admin_bp.route("/answers")
def answers():
    db = get_db()
    rows = db.execute("""
        SELECT a.id, a.question_id, a.name, a.body, a.created_at
        FROM answers a
        ORDER BY a.created_at DESC
        LIMIT 200
    """).fetchall()

    # Make a simple text excerpt without HTML for display
    cleaned = []
    for r in rows:
        text = (r["body"] or "")
        # naive strip tags for preview
        text = text.replace("<", " ").replace(">", " ")
        cleaned.append({
            "id": r["id"],
            "question_id": r["question_id"],
            "name": r["name"],
            "excerpt": text[:120],
            "created_at": r["created_at"],
        })

    body = render_template_string("""
      <div class="card">
        <h2 class="text-xl font-semibold mb-3">Recent answers</h2>
        <table>
          <thead><tr><th>ID</th><th>QID</th><th>Name</th><th>Excerpt</th><th>Created</th></tr></thead>
          <tbody>
            {% for r in rows %}
              <tr>
                <td class="mono">{{ r.id }}</td>
                <td class="mono">{{ r.question_id }}</td>
                <td>{{ r.name or 'Anonymous' }}</td>
                <td class="text-zinc-600">{{ r.excerpt }}</td>
                <td class="text-zinc-500">{{ r.created_at }}</td>
              </tr>
            {% endfor %}
          </tbody>
        </table>
      </div>
    """, rows=cleaned)
    return render_template_string(ADMIN_BASE, body=body)

@admin_bp.route("/analytics")
def analytics():
    """
    Shows three analytics sections:
      1. Feed views (event_type='feed_view')
      2. Question views (event_type='view'), filterable by question ID
      3. Answer views (event_type='view_answer'), filterable by answer ID
    Each with its own date range and filter form, summary, and Chart.js chart.
    """
    db = get_db()

    # Feed views filters
    feed_start = (request.args.get("feed_start") or "").strip()
    feed_end = (request.args.get("feed_end") or "").strip()
    feed_where = ["event_type = 'feed_view'"]
    feed_params = []
    if feed_start:
        feed_where.append("date(created_at) >= date(?)")
        feed_params.append(feed_start)
    if feed_end:
        feed_where.append("date(created_at) <= date(?)")
        feed_params.append(feed_end)
    feed_where_sql = "WHERE " + " AND ".join(feed_where)
    feed_rows = db.execute(f"""
        SELECT date(created_at) AS d, COUNT(*) AS c
        FROM analytics
        {feed_where_sql}
        GROUP BY date(created_at)
        ORDER BY d ASC
    """, feed_params).fetchall()
    feed_total = sum(r["c"] for r in feed_rows)
    feed_labels = [r["d"] for r in feed_rows]
    feed_values = [r["c"] for r in feed_rows]

    # Question views filters
    q_start = (request.args.get("q_start") or "").strip()
    q_end = (request.args.get("q_end") or "").strip()
    qid = request.args.get("q_qid", type=int)
    q_where = ["event_type = 'view'"]
    q_params = []
    if qid:
        q_where.append("(path = ? OR path LIKE ?)")
        q_params.extend((f"/q/{qid}", f"/q/{qid}/%"))
    if q_start:
        q_where.append("date(created_at) >= date(?)")
        q_params.append(q_start)
    if q_end:
        q_where.append("date(created_at) <= date(?)")
        q_params.append(q_end)
    q_where_sql = "WHERE " + " AND ".join(q_where)
    q_rows = db.execute(f"""
        SELECT date(created_at) AS d, COUNT(*) AS c
        FROM analytics
        {q_where_sql}
        GROUP BY date(created_at)
        ORDER BY d ASC
    """, q_params).fetchall()
    q_total = sum(r["c"] for r in q_rows)
    q_labels = [r["d"] for r in q_rows]
    q_values = [r["c"] for r in q_rows]

    # Answer views filters
    a_start = (request.args.get("a_start") or "").strip()
    a_end = (request.args.get("a_end") or "").strip()
    aid = request.args.get("a_aid", type=int)
    a_where = ["event_type = 'view_answer'"]
    a_params = []
    if aid:
        a_where.append("(path LIKE ?)")
        a_params.append(f"%/a/{aid}%")
    if a_start:
        a_where.append("date(created_at) >= date(?)")
        a_params.append(a_start)
    if a_end:
        a_where.append("date(created_at) <= date(?)")
        a_params.append(a_end)
    a_where_sql = "WHERE " + " AND ".join(a_where)
    a_rows = db.execute(f"""
        SELECT date(created_at) AS d, COUNT(*) AS c
        FROM analytics
        {a_where_sql}
        GROUP BY date(created_at)
        ORDER BY d ASC
    """, a_params).fetchall()
    a_total = sum(r["c"] for r in a_rows)
    a_labels = [r["d"] for r in a_rows]
    a_values = [r["c"] for r in a_rows]

    body = render_template_string("""
      <div class="card space-y-8">
        <h2 class="text-xl font-semibold mb-2">Analytics</h2>

        <!-- Feed Views Section -->
        <section>
          <h3 class="text-lg font-semibold mb-2">Feed Views</h3>
          <form class="flex flex-wrap gap-4 mb-2" method="get">
            <input type="hidden" name="section" value="feed">
            <div>
              <label class="block text-xs text-zinc-500 mb-1">Start (YYYY-MM-DD)</label>
              <input name="feed_start" value="{{ feed_start or '' }}" class="border rounded px-2 py-1 w-full" />
            </div>
            <div>
              <label class="block text-xs text-zinc-500 mb-1">End (YYYY-MM-DD)</label>
              <input name="feed_end" value="{{ feed_end or '' }}" class="border rounded px-2 py-1 w-full" />
            </div>
            <div class="flex items-end">
              <button class="px-3 py-2 rounded bg-zinc-900 text-white">Apply</button>
            </div>
          </form>
          <div class="text-sm text-zinc-600 mb-2">
            Total feed views: <span class="font-semibold text-zinc-900">{{ feed_total }}</span>
          </div>
          <div class="bg-white rounded p-3 mb-2" style="height:220px;">
            <canvas id="feed_chart"></canvas>
          </div>
          <table class="mt-2">
            <thead><tr><th>Date</th><th>Count</th></tr></thead>
            <tbody>
              {% for r in feed_rows %}
                <tr><td>{{ r['d'] }}</td><td>{{ r['c'] }}</td></tr>
              {% endfor %}
            </tbody>
          </table>
        </section>

        <!-- Question Views Section -->
        <section>
          <h3 class="text-lg font-semibold mb-2">Question Views</h3>
          <form class="flex flex-wrap gap-4 mb-2" method="get">
            <input type="hidden" name="section" value="question">
            <div>
              <label class="block text-xs text-zinc-500 mb-1">Question ID</label>
              <input name="q_qid" value="{{ qid or '' }}" class="border rounded px-2 py-1 w-full" />
            </div>
            <div>
              <label class="block text-xs text-zinc-500 mb-1">Start (YYYY-MM-DD)</label>
              <input name="q_start" value="{{ q_start or '' }}" class="border rounded px-2 py-1 w-full" />
            </div>
            <div>
              <label class="block text-xs text-zinc-500 mb-1">End (YYYY-MM-DD)</label>
              <input name="q_end" value="{{ q_end or '' }}" class="border rounded px-2 py-1 w-full" />
            </div>
            <div class="flex items-end">
              <button class="px-3 py-2 rounded bg-zinc-900 text-white">Apply</button>
            </div>
          </form>
          <div class="text-sm text-zinc-600 mb-2">
            Total question views: <span class="font-semibold text-zinc-900">{{ q_total }}</span>
          </div>
          <div class="bg-white rounded p-3 mb-2" style="height:220px;">
            <canvas id="q_chart"></canvas>
          </div>
          <table class="mt-2">
            <thead><tr><th>Date</th><th>Count</th></tr></thead>
            <tbody>
              {% for r in q_rows %}
                <tr><td>{{ r['d'] }}</td><td>{{ r['c'] }}</td></tr>
              {% endfor %}
            </tbody>
          </table>
        </section>

        <!-- Answer Views Section -->
        <section>
          <h3 class="text-lg font-semibold mb-2">Answer Views</h3>
          <form class="flex flex-wrap gap-4 mb-2" method="get">
            <input type="hidden" name="section" value="answer">
            <div>
              <label class="block text-xs text-zinc-500 mb-1">Answer ID</label>
              <input name="a_aid" value="{{ aid or '' }}" class="border rounded px-2 py-1 w-full" />
            </div>
            <div>
              <label class="block text-xs text-zinc-500 mb-1">Start (YYYY-MM-DD)</label>
              <input name="a_start" value="{{ a_start or '' }}" class="border rounded px-2 py-1 w-full" />
            </div>
            <div>
              <label class="block text-xs text-zinc-500 mb-1">End (YYYY-MM-DD)</label>
              <input name="a_end" value="{{ a_end or '' }}" class="border rounded px-2 py-1 w-full" />
            </div>
            <div class="flex items-end">
              <button class="px-3 py-2 rounded bg-zinc-900 text-white">Apply</button>
            </div>
          </form>
          <div class="text-sm text-zinc-600 mb-2">
            Total answer views: <span class="font-semibold text-zinc-900">{{ a_total }}</span>
          </div>
          <div class="bg-white rounded p-3 mb-2" style="height:220px;">
            <canvas id="a_chart"></canvas>
          </div>
          <table class="mt-2">
            <thead><tr><th>Date</th><th>Count</th></tr></thead>
            <tbody>
              {% for r in a_rows %}
                <tr><td>{{ r['d'] }}</td><td>{{ r['c'] }}</td></tr>
              {% endfor %}
            </tbody>
          </table>
        </section>

      </div>
      <script>
        // Feed chart
        new Chart(document.getElementById('feed_chart'), {
          type: 'line',
          data: {
            labels: {{ feed_labels|tojson }},
            datasets: [{ label: 'Feed Views', data: {{ feed_values|tojson }}, borderWidth: 2, fill: false, borderColor:'#2563eb', backgroundColor:'#2563eb' }]
          },
          options: { responsive: true, maintainAspectRatio: false }
        });
        // Question chart
        new Chart(document.getElementById('q_chart'), {
          type: 'line',
          data: {
            labels: {{ q_labels|tojson }},
            datasets: [{ label: 'Question Views', data: {{ q_values|tojson }}, borderWidth: 2, fill: false, borderColor:'#059669', backgroundColor:'#059669' }]
          },
          options: { responsive: true, maintainAspectRatio: false }
        });
        // Answer chart
        new Chart(document.getElementById('a_chart'), {
          type: 'line',
          data: {
            labels: {{ a_labels|tojson }},
            datasets: [{ label: 'Answer Views', data: {{ a_values|tojson }}, borderWidth: 2, fill: false, borderColor:'#eab308', backgroundColor:'#eab308' }]
          },
          options: { responsive: true, maintainAspectRatio: false }
        });
      </script>
    """,
    feed_rows=feed_rows, feed_labels=feed_labels, feed_values=feed_values, feed_total=feed_total, feed_start=feed_start, feed_end=feed_end,
    q_rows=q_rows, q_labels=q_labels, q_values=q_values, q_total=q_total, qid=qid, q_start=q_start, q_end=q_end,
    a_rows=a_rows, a_labels=a_labels, a_values=a_values, a_total=a_total, aid=aid, a_start=a_start, a_end=a_end)
    return render_template_string(ADMIN_BASE, body=body)