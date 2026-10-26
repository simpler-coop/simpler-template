"""Wiki-style database editing prototype (ADR 0001).

Flask + SQL database (SQLite by default; schema is plain SQL portable to Postgres).
Login via GitHub or Google OAuth. Every change is stored as a "proposal" row with
its author. Admins can toggle the review gate on/off, approve/reject proposals and
ban a user (rejecting all their pending edits).
"""
import os
import secrets
import sqlite3
from functools import wraps
from urllib.parse import urlencode

import requests
from flask import Flask, abort, flash, g, redirect, render_template, request, session, url_for

DB_PATH = os.environ.get("DATABASE_PATH", os.path.join(os.path.dirname(__file__), "wiki.db"))
FIELDS = ["name", "website", "location", "description"]  # admin-defined columns

PROVIDERS = {
    "github": {
        "authorize": "https://github.com/login/oauth/authorize",
        "token": "https://github.com/login/oauth/access_token",
        "userinfo": "https://api.github.com/user",
        "scope": "read:user user:email",
    },
    "google": {
        "authorize": "https://accounts.google.com/o/oauth2/v2/auth",
        "token": "https://oauth2.googleapis.com/token",
        "userinfo": "https://openidconnect.googleapis.com/v1/userinfo",
        "scope": "openid email profile",
    },
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
  id INTEGER PRIMARY KEY, provider TEXT NOT NULL, provider_id TEXT NOT NULL,
  name TEXT, email TEXT, is_admin INTEGER DEFAULT 0, is_banned INTEGER DEFAULT 0,
  created_at TEXT DEFAULT CURRENT_TIMESTAMP, UNIQUE(provider, provider_id));
CREATE TABLE IF NOT EXISTS entries (
  id INTEGER PRIMARY KEY, name TEXT, website TEXT, location TEXT, description TEXT,
  updated_by INTEGER REFERENCES users(id), updated_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS proposals (
  id INTEGER PRIMARY KEY, entry_id INTEGER REFERENCES entries(id),
  name TEXT, website TEXT, location TEXT, description TEXT,
  author_id INTEGER NOT NULL REFERENCES users(id),
  status TEXT NOT NULL DEFAULT 'pending',  -- pending | approved | rejected
  reviewed_by INTEGER REFERENCES users(id),
  created_at TEXT DEFAULT CURRENT_TIMESTAMP, reviewed_at TEXT);
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT);
INSERT OR IGNORE INTO settings VALUES ('review_gate', '1');
"""


def create_app(test_config=None):
    app = Flask(__name__)
    app.config.update(
        SECRET_KEY=os.environ.get("SECRET_KEY", secrets.token_hex(16)),
        DATABASE_PATH=DB_PATH,
        ADMIN_EMAILS={e.strip().lower() for e in os.environ.get("ADMIN_EMAILS", "").split(",") if e.strip()},
        ADMIN_GITHUB_LOGINS={e.strip().lower() for e in os.environ.get("ADMIN_GITHUB_LOGINS", "").split(",") if e.strip()},
        DEV_LOGIN=os.environ.get("DEV_LOGIN") == "1",  # mock login for local testing ONLY
    )
    if test_config:
        app.config.update(test_config)

    def db():
        if "db" not in g:
            g.db = sqlite3.connect(app.config["DATABASE_PATH"])
            g.db.row_factory = sqlite3.Row
            g.db.execute("PRAGMA foreign_keys = ON")
        return g.db

    @app.teardown_appcontext
    def close_db(_exc):
        conn = g.pop("db", None)
        if conn:
            conn.close()

    with app.app_context():
        db().executescript(SCHEMA)
        db().commit()

    def setting(key):
        return db().execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()["value"]

    def current_user():
        uid = session.get("uid")
        if not uid:
            return None
        return db().execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()

    def login_required(fn):
        @wraps(fn)
        def wrapper(*a, **kw):
            if not current_user():
                return redirect(url_for("login"))
            return fn(*a, **kw)
        return wrapper

    def admin_required(fn):
        @wraps(fn)
        def wrapper(*a, **kw):
            u = current_user()
            if not u or not u["is_admin"]:
                abort(403)
            return fn(*a, **kw)
        return wrapper

    @app.context_processor
    def inject():
        return {"user": current_user(), "review_gate": setting("review_gate") == "1",
                "fields": FIELDS, "dev_login": app.config["DEV_LOGIN"]}

    def upsert_user(provider, provider_id, name, email, login=None):
        email = (email or "").lower()
        is_admin = int(email in app.config["ADMIN_EMAILS"] or
                       (provider == "github" and (login or "").lower() in app.config["ADMIN_GITHUB_LOGINS"]))
        d = db()
        d.execute("""INSERT INTO users(provider, provider_id, name, email, is_admin) VALUES (?,?,?,?,?)
                     ON CONFLICT(provider, provider_id) DO UPDATE SET name=excluded.name,
                     email=excluded.email, is_admin=MAX(users.is_admin, excluded.is_admin)""",
                  (provider, str(provider_id), name, email, is_admin))
        d.commit()
        return d.execute("SELECT id FROM users WHERE provider=? AND provider_id=?",
                         (provider, str(provider_id))).fetchone()["id"]

    def apply_proposal(p, reviewer_id):
        d = db()
        vals = [p[f] for f in FIELDS]
        if p["entry_id"]:
            d.execute(f"UPDATE entries SET {', '.join(f + '=?' for f in FIELDS)}, updated_by=?, "
                      "updated_at=CURRENT_TIMESTAMP WHERE id=?", (*vals, p["author_id"], p["entry_id"]))
        else:
            cur = d.execute(f"INSERT INTO entries({', '.join(FIELDS)}, updated_by) VALUES (?,?,?,?,?)",
                            (*vals, p["author_id"]))
            d.execute("UPDATE proposals SET entry_id=? WHERE id=?", (cur.lastrowid, p["id"]))
        d.execute("UPDATE proposals SET status='approved', reviewed_by=?, reviewed_at=CURRENT_TIMESTAMP "
                  "WHERE id=?", (reviewer_id, p["id"]))
        d.commit()

    # ---------- auth ----------
    @app.route("/login")
    def login():
        enabled = [p for p in PROVIDERS if os.environ.get(f"{p.upper()}_CLIENT_ID")]
        return render_template("login.html", providers=enabled)

    @app.route("/login/<provider>")
    def oauth_start(provider):
        if provider not in PROVIDERS or not os.environ.get(f"{provider.upper()}_CLIENT_ID"):
            abort(404)
        state = secrets.token_urlsafe(16)
        session["oauth_state"] = state
        params = {"client_id": os.environ[f"{provider.upper()}_CLIENT_ID"],
                  "redirect_uri": url_for("oauth_callback", provider=provider, _external=True),
                  "scope": PROVIDERS[provider]["scope"], "state": state, "response_type": "code"}
        return redirect(PROVIDERS[provider]["authorize"] + "?" + urlencode(params))

    @app.route("/auth/<provider>/callback")
    def oauth_callback(provider):
        if provider not in PROVIDERS or request.args.get("state") != session.pop("oauth_state", None):
            abort(400)
        cfg = PROVIDERS[provider]
        tok = requests.post(cfg["token"], headers={"Accept": "application/json"}, timeout=10, data={
            "client_id": os.environ[f"{provider.upper()}_CLIENT_ID"],
            "client_secret": os.environ[f"{provider.upper()}_CLIENT_SECRET"],
            "code": request.args["code"], "grant_type": "authorization_code",
            "redirect_uri": url_for("oauth_callback", provider=provider, _external=True)}).json()
        info = requests.get(cfg["userinfo"], timeout=10,
                            headers={"Authorization": f"Bearer {tok['access_token']}"}).json()
        if provider == "github":
            email = info.get("email")
            if not email:
                emails = requests.get("https://api.github.com/user/emails", timeout=10,
                                      headers={"Authorization": f"Bearer {tok['access_token']}"}).json()
                email = next((e["email"] for e in emails if e.get("primary")), None)
            uid = upsert_user("github", info["id"], info.get("name") or info["login"], email, info["login"])
        else:
            uid = upsert_user("google", info["sub"], info.get("name"), info.get("email"))
        session["uid"] = uid
        return redirect(url_for("index"))

    @app.route("/dev-login", methods=["POST"])
    def dev_login():
        if not app.config["DEV_LOGIN"]:
            abort(404)
        name = request.form["name"].strip()
        email = f"{name.lower()}@example.test"
        session["uid"] = upsert_user("dev", name.lower(), name, email)
        if request.form.get("admin"):
            db().execute("UPDATE users SET is_admin=1 WHERE id=?", (session["uid"],))
            db().commit()
        return redirect(url_for("index"))

    @app.route("/logout")
    def logout():
        session.clear()
        return redirect(url_for("index"))

    # ---------- data ----------
    @app.route("/")
    def index():
        rows = db().execute("""SELECT e.*, u.name AS editor FROM entries e
                               LEFT JOIN users u ON u.id=e.updated_by ORDER BY e.id""").fetchall()
        return render_template("index.html", entries=rows)

    @app.route("/entry/new", methods=["GET", "POST"])
    @app.route("/entry/<int:entry_id>/edit", methods=["GET", "POST"])
    @login_required
    def edit(entry_id=None):
        u = current_user()
        entry = None
        if entry_id:
            entry = db().execute("SELECT * FROM entries WHERE id=?", (entry_id,)).fetchone() or abort(404)
        if request.method == "POST":
            if u["is_banned"]:
                abort(403)
            vals = [request.form.get(f, "").strip() for f in FIELDS]
            d = db()
            cur = d.execute(f"INSERT INTO proposals(entry_id, {', '.join(FIELDS)}, author_id) "
                            "VALUES (?,?,?,?,?,?)", (entry_id, *vals, u["id"]))
            d.commit()
            p = d.execute("SELECT * FROM proposals WHERE id=?", (cur.lastrowid,)).fetchone()
            if setting("review_gate") == "1" and not u["is_admin"]:
                flash("Thanks! Your change was sent to an admin for review.")
            else:
                apply_proposal(p, u["id"])
                flash("Saved.")
            return redirect(url_for("index"))
        return render_template("edit.html", entry=entry)

    @app.route("/entry/<int:entry_id>/history")
    def history(entry_id):
        rows = db().execute("""SELECT p.*, u.name AS author FROM proposals p JOIN users u ON u.id=p.author_id
                               WHERE p.entry_id=? ORDER BY p.id DESC""", (entry_id,)).fetchall()
        return render_template("history.html", rows=rows, entry_id=entry_id)

    # ---------- admin ----------
    @app.route("/admin")
    @admin_required
    def admin():
        pending = db().execute("""SELECT p.*, u.name AS author, e.name AS old_name FROM proposals p
                                  JOIN users u ON u.id=p.author_id LEFT JOIN entries e ON e.id=p.entry_id
                                  WHERE p.status='pending' ORDER BY p.id""").fetchall()
        users = db().execute("SELECT * FROM users ORDER BY id").fetchall()
        return render_template("admin.html", pending=pending, users=users)

    @app.route("/admin/gate", methods=["POST"])
    @admin_required
    def toggle_gate():
        db().execute("UPDATE settings SET value=? WHERE key='review_gate'",
                     ("1" if request.form.get("on") == "1" else "0",))
        db().commit()
        return redirect(url_for("admin"))

    @app.route("/admin/proposal/<int:pid>/<action>", methods=["POST"])
    @admin_required
    def review(pid, action):
        p = db().execute("SELECT * FROM proposals WHERE id=? AND status='pending'", (pid,)).fetchone() or abort(404)
        if action == "approve":
            apply_proposal(p, current_user()["id"])
        elif action == "reject":
            db().execute("UPDATE proposals SET status='rejected', reviewed_by=?, reviewed_at=CURRENT_TIMESTAMP "
                         "WHERE id=?", (current_user()["id"], pid))
            db().commit()
        else:
            abort(400)
        return redirect(url_for("admin"))

    @app.route("/admin/user/<int:uid>/ban", methods=["POST"])
    @admin_required
    def ban(uid):
        d = db()
        d.execute("UPDATE users SET is_banned=1 WHERE id=?", (uid,))
        d.execute("UPDATE proposals SET status='rejected', reviewed_by=? WHERE author_id=? AND status='pending'",
                  (current_user()["id"], uid))
        d.commit()
        return redirect(url_for("admin"))

    return app


if __name__ == "__main__":
    create_app().run(host="127.0.0.1", port=int(os.environ.get("PORT", 5000)), debug=False)
