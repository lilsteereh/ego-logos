from flask import Blueprint, g, render_template_string, request, redirect, url_for, session, abort
import os, sqlite3
from datetime import datetime
import json

def log_event(event_type, path):
    try:
        db = get_db()
        db.execute("INSERT INTO analytics (event_type, path, created_at) VALUES (?,?,?)", (event_type, path, datetime.utcnow()))
        db.commit()
    except Exception as e:
        print(f"Analytics log failed: {e}")

admin_bp = Blueprint("admin", __name__)

# ---------- Shared Layout ----------
BASE = """
<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Admin – Ego Logos</title>
    <link rel="icon" type="image/png" href="/static/e.png">
    <script src="https://cdn.tailwindcss.com"></script>
  </head>
  <body class="bg-zinc-50 text-zinc-900">
    <div class="max-w-4xl mx-auto p-6">
      <header class="flex items-center justify-between mb-8">
        <h1 class="text-2xl font-bold">
          <a href="{{ url_for('admin.dashboard') }}">Admin Panel</a>
        </h1>
        {% if session.get('admin_logged_in') %}
        <nav class="space-x-4">
          <a class="hover:underline" href="{{ url_for('admin.questions') }}">Questions</a>
          <a class="hover:underline" href="{{ url_for('admin.answers') }}">Answers</a>
          <a class="hover:underline" href="{{ url_for('admin.suggestions') }}">Suggestions</a>
          <a class="hover:underline" href="{{ url_for('admin.analytics') }}">Analytics</a>
          <a class="hover:underline text-red-600" href="{{ url_for('admin.logout') }}">Logout</a>
        </nav>
        {% endif %}
      </header>
      {{ body|safe }}
    </div>
  </body>
</html>
"""

# ---------- DB helper ----------
def get_db():
    from Ego import get_db   # reuse main app’s DB connection
    return get_db()

# ---------- Auth ----------
ADMIN_USER = os.environ.get("ADMIN_USER", "omar")
ADMIN_PASS = os.environ.get("ADMIN_PASSWORD", "omar")

def require_login():
    if not session.get("admin_logged_in"):
        abort(403)

# ---------- Routes ----------
@admin_bp.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        u = request.form.get("username", "")
        p = request.form.get("password", "")
        if u == ADMIN_USER and p == ADMIN_PASS:
            session["admin_logged_in"] = True
            return redirect(url_for("admin.dashboard"))
        else:
            body = "<p class='text-red-600 mb-4'>Invalid credentials</p>" + LOGIN_FORM
            return render_template_string(BASE, body=body)
    return render_template_string(BASE, body=LOGIN_FORM)

@admin_bp.route("/logout")
def logout():
    session.pop("admin_logged_in", None)
    return redirect(url_for("admin.login"))

@admin_bp.route("/")
def dashboard():
    require_login()
    body = """
      <div class="space-y-4">
        <p class="text-lg">Welcome to the admin dashboard.</p>
        <ul class="list-disc pl-6">
          <li><a class="text-blue-600 hover:underline" href="{{ url_for('admin.questions') }}">Manage Questions</a></li>
          <li><a class="text-blue-600 hover:underline" href="{{ url_for('admin.answers') }}">Manage Answers</a></li>
          <li><a class="text-blue-600 hover:underline" href="{{ url_for('admin.suggestions') }}">View Suggestions</a></li>
        </ul>
      </div>
    """
    return render_template_string(BASE, body=body)

@admin_bp.route("/questions")
def questions():
    require_login()
    db = get_db()
    rows = db.execute("SELECT id,title,created_at FROM questions ORDER BY id DESC").fetchall()
    body = render_template_string("""
      <h2 class="text-xl font-semibold mb-4">Questions</h2>
      <ul class="space-y-3">
      {% for q in rows %}
        <li class="bg-white p-4 rounded-xl shadow flex justify-between">
          <span>{{ q['id'] }} – {{ q['title'] }} ({{ q['created_at'] }})</span>
          <a class="text-red-600 hover:underline" href="{{ url_for('admin.delete_question', qid=q['id']) }}">Delete</a>
        </li>
      {% else %}
        <li class="text-zinc-600">No questions yet.</li>
      {% endfor %}
      </ul>
    """, rows=rows)
    return render_template_string(BASE, body=body)

@admin_bp.route("/answers")
def answers():
    require_login()
    db = get_db()
    rows = db.execute("""
        SELECT a.id, a.body, a.created_at, q.title AS qtitle
        FROM answers a JOIN questions q ON a.question_id=q.id
        ORDER BY a.id DESC
    """).fetchall()
    body = render_template_string("""
      <h2 class="text-xl font-semibold mb-4">Answers</h2>
      <ul class="space-y-3">
      {% for a in rows %}
        <li class="bg-white p-4 rounded-xl shadow flex justify-between">
          <span>{{ a['id'] }} – for [{{ a['qtitle'] }}]: {{ a['body'][:50] }}</span>
          <a class="text-red-600 hover:underline" href="{{ url_for('admin.delete_answer', aid=a['id']) }}">Delete</a>
        </li>
      {% else %}
        <li class="text-zinc-600">No answers yet.</li>
      {% endfor %}
      </ul>
    """, rows=rows)
    return render_template_string(BASE, body=body)

@admin_bp.route("/suggestions")
def suggestions():
    require_login()
    db = get_db()
    rows = db.execute("SELECT id, body, created_at FROM suggestions ORDER BY id DESC").fetchall()
    body = render_template_string("""
      <h2 class="text-xl font-semibold mb-4">Suggestions</h2>
      <ul class="space-y-3">
      {% for s in rows %}
        <li class="bg-white p-4 rounded-xl shadow">
          {{ s['id'] }} – {{ s['body'] }} ({{ s['created_at'] }})
        </li>
      {% else %}
        <li class="text-zinc-600">No suggestions yet.</li>
      {% endfor %}
      </ul>
    """, rows=rows)
    return render_template_string(BASE, body=body)

# ---------- Delete actions ----------
@admin_bp.route("/delete-question/<int:qid>")
def delete_question(qid):
    require_login()
    db = get_db()
    db.execute("DELETE FROM answers WHERE question_id=?", (qid,))
    db.execute("DELETE FROM votes WHERE question_id=?", (qid,))
    db.execute("DELETE FROM questions WHERE id=?", (qid,))
    db.commit()
    return redirect(url_for("admin.questions"))

@admin_bp.route("/delete-answer/<int:aid>")
def delete_answer(aid):
    require_login()
    db = get_db()
    db.execute("DELETE FROM votes WHERE answer_id=?", (aid,))
    db.execute("DELETE FROM answers WHERE id=?", (aid,))
    db.commit()
    return redirect(url_for("admin.answers"))

@admin_bp.route("/analytics")
def analytics():
    import json
    from datetime import date, timedelta

    db = get_db()

    # --- Ensure analytics table exists ---
    try:
        db.execute("""
            CREATE TABLE IF NOT EXISTS analytics (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_type TEXT NOT NULL,
                path TEXT,
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """)
        db.execute("""
            CREATE INDEX IF NOT EXISTS idx_analytics_type_date 
            ON analytics(event_type, created_at)
        """)
        db.commit()
    except Exception as e:
        print("Analytics table creation error:", e)

    # --- Parameters ---
    start = request.args.get("start")
    end = request.args.get("end")
    item_id = request.args.get("item_id", "").strip()

    today = date.today()
    if not start:
        start = (today - timedelta(days=30)).isoformat()
    if not end:
        end = today.isoformat()

    params = [start, end]
    filter_clause = ""
    if item_id:
        if item_id.isdigit():
            filter_clause = "AND (path LIKE ? OR path LIKE ?)"
            params.extend([f"/q/{item_id}%", f"/q/%/a/{item_id}%"])

    # --- Aggregate data ---
    try:
        rows = db.execute(f"""
            SELECT date(created_at) AS day,
                   SUM(CASE WHEN event_type='view' THEN 1 ELSE 0 END) AS views,
                   SUM(CASE WHEN event_type LIKE 'vote%' THEN 1 ELSE 0 END) AS votes
            FROM analytics
            WHERE created_at BETWEEN ? AND ?
            {filter_clause}
            GROUP BY day
            ORDER BY day;
        """, params).fetchall()

        total_views = db.execute(f"SELECT COUNT(*) FROM analytics WHERE event_type='view' AND created_at BETWEEN ? AND ? {filter_clause}", params).fetchone()[0]
        total_votes = db.execute(f"SELECT COUNT(*) FROM analytics WHERE event_type LIKE 'vote%' AND created_at BETWEEN ? AND ? {filter_clause}", params).fetchone()[0]
    except Exception as e:
        rows, total_views, total_votes = [], 0, 0

    # --- Prepare chart data ---
    dates = [r["day"] for r in rows]
    views = [r["views"] for r in rows]
    votes = [r["votes"] for r in rows]

    html = f"""
    <div class="bg-white p-6 rounded-2xl shadow-sm">
      <h1 class="text-2xl font-bold mb-4">Analytics Dashboard</h1>

      <form method="get" class="flex flex-wrap gap-2 mb-6">
        <input type="date" name="start" value="{start}" class="border rounded px-2 py-1">
        <input type="date" name="end" value="{end}" class="border rounded px-2 py-1">
        <input type="text" name="item_id" value="{item_id}" placeholder="Question/Answer ID" class="border rounded px-2 py-1" />
        <button class="px-3 py-1 bg-zinc-900 text-white rounded-xl">Filter</button>
      </form>

      <div class="grid grid-cols-2 gap-4 mb-4">
        <div class="p-3 bg-zinc-100 rounded-lg text-center">
          <div class="text-sm text-zinc-600">Total Views</div>
          <div class="text-xl font-semibold">{total_views}</div>
        </div>
        <div class="p-3 bg-zinc-100 rounded-lg text-center">
          <div class="text-sm text-zinc-600">Total Votes</div>
          <div class="text-xl font-semibold">{total_votes}</div>
        </div>
      </div>

      <canvas id="analyticsChart" height="120"></canvas>
      <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
      <script>
        const ctx = document.getElementById('analyticsChart');
        new Chart(ctx, {{
          type: 'line',
          data: {{
            labels: {json.dumps(dates)},
            datasets: [
              {{ label: 'Views', data: {json.dumps(views)}, borderColor: '#f59e0b', fill: false }},
              {{ label: 'Votes', data: {json.dumps(votes)}, borderColor: '#3b82f6', fill: false }}
            ]
          }},
          options: {{ scales: {{ y: {{ beginAtZero: true }} }} }}
        }});
      </script>
    </div>
    """

    return render_template_string(BASE, body=html)

# ---------- Login form ----------
LOGIN_FORM = """
<form method="post" class="space-y-4 max-w-sm mx-auto bg-white p-6 rounded-xl shadow">
  <div>
    <label class="block text-sm text-zinc-700">User</label>
    <input name="username" class="w-full border border-zinc-300 rounded px-3 py-2 focus:outline-none focus:ring-2 focus:ring-zinc-300">
  </div>
  <div>
    <label class="block text-sm text-zinc-700">Pass</label>
    <input type="password" name="password" class="w-full border border-zinc-300 rounded px-3 py-2 focus:outline-none focus:ring-2 focus:ring-zinc-300">
  </div>
  <button type="submit" class="w-full bg-zinc-900 text-white py-2 rounded hover:bg-zinc-800">Login</button>
</form>
"""