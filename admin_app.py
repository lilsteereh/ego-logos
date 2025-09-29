from flask import Blueprint, g, render_template_string, request, redirect, url_for, session, abort
import os, sqlite3
from datetime import datetime

admin_bp = Blueprint("admin", __name__)

# ---------- DB helper ----------
def get_db():
    from Ego import get_db   # use the same connection helper
    return get_db()

# ---------- Auth ----------
ADMIN_USER = os.environ.get("ADMIN_USER", "omar")
ADMIN_PASS = os.environ.get("ADMIN_PASSWORD", "omar")

def require_login():
    if not session.get("admin_logged_in"):
        abort(403)

@admin_bp.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        u = request.form.get("username", "")
        p = request.form.get("password", "")
        if u == ADMIN_USER and p == ADMIN_PASS:
            session["admin_logged_in"] = True
            return redirect(url_for("admin.dashboard"))
        else:
            return render_template_string("<p>Invalid credentials</p>" + LOGIN_FORM)
    return render_template_string(LOGIN_FORM)

@admin_bp.route("/logout")
def logout():
    session.pop("admin_logged_in", None)
    return redirect(url_for("admin.login"))

# ---------- Pages ----------
@admin_bp.route("/")
def dashboard():
    require_login()
    return render_template_string("""
    <h1>Admin Dashboard</h1>
    <ul>
      <li><a href="{{ url_for('admin.questions') }}">Manage Questions</a></li>
      <li><a href="{{ url_for('admin.answers') }}">Manage Answers</a></li>
      <li><a href="{{ url_for('admin.suggestions') }}">View Suggestions</a></li>
      <li><a href="{{ url_for('admin.logout') }}">Logout</a></li>
    </ul>
    """)

@admin_bp.route("/questions")
def questions():
    require_login()
    db = get_db()
    rows = db.execute("SELECT id,title,created_at FROM questions ORDER BY id DESC").fetchall()
    return render_template_string("""
    <h2>Questions</h2>
    <ul>
    {% for q in rows %}
      <li>
        {{ q['id'] }} – {{ q['title'] }} ({{ q['created_at'] }})
        <a href="{{ url_for('admin.delete_question', qid=q['id']) }}">Delete</a>
      </li>
    {% endfor %}
    </ul>
    <a href="{{ url_for('admin.dashboard') }}">Back</a>
    """, rows=rows)

@admin_bp.route("/answers")
def answers():
    require_login()
    db = get_db()
    rows = db.execute("""
        SELECT a.id, a.body, a.created_at, q.title AS qtitle
        FROM answers a JOIN questions q ON a.question_id=q.id
        ORDER BY a.id DESC
    """).fetchall()
    return render_template_string("""
    <h2>Answers</h2>
    <ul>
    {% for a in rows %}
      <li>
        {{ a['id'] }} – for [{{ a['qtitle'] }}]: {{ a['body'][:50] }}
        <a href="{{ url_for('admin.delete_answer', aid=a['id']) }}">Delete</a>
      </li>
    {% endfor %}
    </ul>
    <a href="{{ url_for('admin.dashboard') }}">Back</a>
    """, rows=rows)

@admin_bp.route("/suggestions")
def suggestions():
    require_login()
    db = get_db()
    rows = db.execute("SELECT id, body, created_at FROM suggestions ORDER BY id DESC").fetchall()
    return render_template_string("""
    <h2>Suggestions</h2>
    <ul>
    {% for s in rows %}
      <li>{{ s['id'] }} – {{ s['body'] }} ({{ s['created_at'] }})</li>
    {% endfor %}
    </ul>
    <a href="{{ url_for('admin.dashboard') }}">Back</a>
    """, rows=rows)

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

# ---------- Simple login form ----------
LOGIN_FORM = """
<form method="post">
  <label>User: <input name="username"></label><br>
  <label>Pass: <input type="password" name="password"></label><br>
  <button type="submit">Login</button>
</form>
"""