from flask import Flask, g, render_template, request, redirect, url_for, send_from_directory
import os, sqlite3


app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "devkey")

DATABASE = "/var/data/ego.db"

def get_db():
    if 'db' not in g:
        g.db = sqlite3.connect(DATABASE)
        g.db.row_factory = sqlite3.Row
    return g.db


def log_event(event_type, path):
    db = get_db()
    db.execute("""
        CREATE TABLE IF NOT EXISTS analytics (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_type TEXT,
            path TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    db.execute("INSERT INTO analytics (event_type, path) VALUES (?, ?)", (event_type, path))
    db.commit()

def close_db(e=None):
    db = g.pop('db', None)
    if db is not None:
        db.close()

app.teardown_appcontext(close_db)

def init_db():
    db = get_db()
    with app.open_resource('schema.sql') as f:
        db.executescript(f.read().decode('utf8'))
    db.commit()

if __name__ == "__main__":
    init_db()

@app.route("/")
def index():
    log_event('view', request.path)
    db = get_db()
    questions = db.execute("SELECT id, title FROM questions ORDER BY id DESC").fetchall()
    return render_template("index.html", questions=questions)

@app.route("/q/<int:qid>")
def question(qid):
    log_event('view', f"/q/{qid}")
    db = get_db()
    q = db.execute("SELECT * FROM questions WHERE id=?", (qid,)).fetchone()
    if q is None:
        return "Question not found", 404
    answers = db.execute("SELECT * FROM answers WHERE question_id=? ORDER BY id DESC", (qid,)).fetchall()
    return render_template("question.html", question=q, answers=answers)

@app.route("/ask", methods=["GET", "POST"])
def ask():
    if request.method == "GET":
        log_event('view', '/ask')
        return render_template("ask.html")
    else:
        title = request.form.get("title")
        body = request.form.get("body")
        db = get_db()
        cursor = db.execute("INSERT INTO questions (title, body) VALUES (?, ?)", (title, body))
        db.commit()
        qid = cursor.lastrowid
        log_event('post-question', f"/q/{qid}")
        return redirect(url_for("question", qid=qid))

@app.route("/q/<int:qid>/answer", methods=["POST"])
def answer(qid):
    body = request.form.get("body")
    db = get_db()
    db.execute("INSERT INTO answers (question_id, body) VALUES (?, ?)", (qid, body))
    db.commit()
    log_event('post-answer', f"/q/{qid}")
    return redirect(url_for("question", qid=qid))

@app.route("/suggest", methods=["GET", "POST"])
def suggest():
    if request.method == "GET":
        log_event('view', '/suggest')
        return render_template("suggest.html")
    else:
        body = request.form.get("body")
        db = get_db()
        db.execute("INSERT INTO suggestions (body) VALUES (?)", (body,))
        db.commit()
        log_event('suggest', '/suggest')
        return redirect(url_for("index"))

@app.route("/vote-question/<int:qid>", methods=["POST"])
def vote_question(qid):
    db = get_db()
    # Voting logic here (toggle vote)
    # Assuming vote added or removed successfully:
    log_event('vote-question', f"/q/{qid}")
    return redirect(url_for("question", qid=qid))

@app.route("/vote-answer/<int:aid>", methods=["POST"])
def vote_answer(aid):
    db = get_db()
    # Get question id for answer
    qid_row = db.execute("SELECT question_id FROM answers WHERE id=?", (aid,)).fetchone()
    if qid_row is None:
        return "Answer not found", 404
    qid = qid_row["question_id"]
    # Voting logic here (toggle vote)
    # Assuming vote added or removed successfully:
    log_event('vote-answer', f"/q/{qid}/a/{aid}")
    return redirect(url_for("question", qid=qid))

@app.route("/uploads/<filename>")
def uploaded_file(filename):
    return send_from_directory("/var/data/uploads", filename)