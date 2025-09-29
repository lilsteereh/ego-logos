from flask import Blueprint, g, render_template_string, request, redirect, url_for, session, abort
import os, sqlite3
from datetime import datetime

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