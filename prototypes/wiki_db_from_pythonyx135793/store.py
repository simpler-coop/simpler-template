"""SQLite data layer for the wiki-style database editing prototype."""
import secrets
import sqlite3
import threading
import time

FIELDS = [("name", "Name"), ("website", "Website"), ("contact", "Contact"), ("notes", "Notes")]
FIELD_KEYS = [key for key, _ in FIELDS]

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY, provider TEXT NOT NULL, provider_id TEXT NOT NULL,
    name TEXT NOT NULL, is_admin INTEGER NOT NULL DEFAULT 0, banned INTEGER NOT NULL DEFAULT 0,
    UNIQUE (provider, provider_id));
CREATE TABLE IF NOT EXISTS sessions (
    sid TEXT PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id), csrf TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS records (
    id INTEGER PRIMARY KEY, name TEXT NOT NULL DEFAULT '', website TEXT NOT NULL DEFAULT '',
    contact TEXT NOT NULL DEFAULT '', notes TEXT NOT NULL DEFAULT '',
    visible INTEGER NOT NULL DEFAULT 1, created_by INTEGER REFERENCES users(id));
CREATE TABLE IF NOT EXISTS contributions (
    id INTEGER PRIMARY KEY, record_id INTEGER NOT NULL REFERENCES records(id),
    field TEXT NOT NULL, old_value TEXT NOT NULL, new_value TEXT NOT NULL,
    user_id INTEGER NOT NULL REFERENCES users(id),
    status TEXT NOT NULL CHECK (status IN ('pending', 'applied', 'rejected', 'reverted')),
    reviewed_by INTEGER REFERENCES users(id), created_at REAL NOT NULL);
"""

SEED = [
    ("Simpler Co-op", "https://github.com/simpler-coop", "", ""),
    ("Example Mutual Aid Network", "", "", "Add a website and a contact!"),
    ("Community Land Trust", "", "", ""),
]


class EditError(Exception):
    """A user-facing problem with a submitted edit."""


class Store:
    def __init__(self, path=":memory:"):
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.lock = threading.RLock()
        self.cond = threading.Condition()
        self.version = 0
        with self.lock:
            self.db.executescript(SCHEMA)
            self.db.execute("INSERT OR IGNORE INTO settings VALUES ('review_required', '1')")
            if not self.db.execute("SELECT 1 FROM records").fetchone():
                self.db.executemany(
                    "INSERT INTO records (name, website, contact, notes) VALUES (?, ?, ?, ?)", SEED)
            self.db.commit()

    # -- live updates --------------------------------------------------
    def _changed(self):
        self.db.commit()
        with self.cond:
            self.version += 1
            self.cond.notify_all()

    def wait_for_change(self, seen, timeout):
        with self.cond:
            if self.version == seen:
                self.cond.wait(timeout)
            return self.version

    # -- users and sessions --------------------------------------------
    def login(self, provider, provider_id, name, is_admin):
        """Create or update the account for a login. Returns (user, sid, csrf), or None if banned."""
        with self.lock:
            self.db.execute(
                "INSERT INTO users (provider, provider_id, name, is_admin) VALUES (?, ?, ?, ?) "
                "ON CONFLICT (provider, provider_id) DO UPDATE SET name = excluded.name, "
                "is_admin = excluded.is_admin OR users.is_admin",
                (provider, str(provider_id), name, int(is_admin)))
            user = self.db.execute("SELECT * FROM users WHERE provider = ? AND provider_id = ?",
                                   (provider, str(provider_id))).fetchone()
            if user["banned"]:
                self.db.commit()
                return None
            sid, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(24)
            self.db.execute("INSERT INTO sessions VALUES (?, ?, ?)", (sid, user["id"], csrf))
            self.db.commit()
            return user, sid, csrf

    def session(self, sid):
        """Return (user, csrf) for a live session of a non-banned user, else None."""
        with self.lock:
            row = self.db.execute(
                "SELECT u.*, s.csrf AS csrf FROM sessions s JOIN users u ON u.id = s.user_id "
                "WHERE s.sid = ? AND u.banned = 0", (sid,)).fetchone()
        return (row, row["csrf"]) if row else None

    def logout(self, sid):
        with self.lock:
            self.db.execute("DELETE FROM sessions WHERE sid = ?", (sid,))
            self.db.commit()

    def users(self):
        with self.lock:
            return self.db.execute(
                "SELECT u.*, (SELECT COUNT(*) FROM contributions c WHERE c.user_id = u.id "
                "AND c.status IN ('applied', 'pending')) AS edits FROM users u ORDER BY u.id").fetchall()

    # -- settings ------------------------------------------------------
    def review_required(self):
        with self.lock:
            return self.db.execute(
                "SELECT value FROM settings WHERE key = 'review_required'").fetchone()[0] == "1"

    def set_review_required(self, on):
        with self.lock:
            self.db.execute("UPDATE settings SET value = ? WHERE key = 'review_required'",
                            ("1" if on else "0",))
            self._changed()

    # -- reading -------------------------------------------------------
    def records(self):
        with self.lock:
            return self.db.execute("SELECT * FROM records WHERE visible = 1 ORDER BY id").fetchall()

    def record(self, rid):
        with self.lock:
            return self.db.execute("SELECT * FROM records WHERE id = ? AND visible = 1",
                                   (rid,)).fetchone()

    def pending_for(self, user_id):
        with self.lock:
            return self.db.execute(
                "SELECT COUNT(*) FROM contributions WHERE user_id = ? AND status = 'pending'",
                (user_id,)).fetchone()[0]

    def contributions(self, status=None, limit=50):
        sql = ("SELECT c.*, u.name AS user_name, r.name AS record_name FROM contributions c "
               "JOIN users u ON u.id = c.user_id JOIN records r ON r.id = c.record_id ")
        args = ()
        if status:
            sql, args = sql + "WHERE c.status = ? ", (status,)
        with self.lock:
            return self.db.execute(sql + "ORDER BY c.id DESC LIMIT ?", args + (limit,)).fetchall()

    # -- writing -------------------------------------------------------
    @staticmethod
    def _clean(values):
        return {k: " ".join(str(values.get(k, "")).split())[:500] for k in FIELD_KEYS if k in values}

    def _contribute(self, user, rid, field, old, new):
        """Record one cell change; apply at once unless the review gate holds it back."""
        direct = bool(user["is_admin"]) or not self.review_required()
        self.db.execute(
            "INSERT INTO contributions (record_id, field, old_value, new_value, user_id, status, "
            "reviewed_by, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (rid, field, old, new, user["id"], "applied" if direct else "pending",
             user["id"] if direct else None, time.time()))
        if direct:
            self.db.execute(f"UPDATE records SET {field} = ? WHERE id = ?", (new, rid))
        return direct

    def edit_record(self, user, rid, values, base):
        """Submit edits to a record. `base` holds the values the editor was looking at.

        Returns (applied, held_for_review). Raises EditError if someone changed a cell meanwhile.
        """
        values = self._clean(values)
        with self.lock:
            rec = self.record(rid)
            if rec is None:
                raise EditError("That entry does not exist.")
            todo = [(k, v) for k, v in values.items() if v != rec[k]]
            for k, _ in todo:
                if base.get(k, "") != rec[k]:
                    raise EditError(f"Someone else just changed “{dict(FIELDS)[k]}”. "
                                    "Please reload the page and try again.")
            results = [self._contribute(user, rid, k, rec[k], v) for k, v in todo]
            self._changed()
        return results.count(True), results.count(False)

    def add_record(self, user, values):
        values = self._clean(values)
        if not values.get("name"):
            raise EditError("Please give the new entry a name.")
        direct = bool(user["is_admin"]) or not self.review_required()
        with self.lock:
            rid = self.db.execute("INSERT INTO records (visible, created_by) VALUES (?, ?)",
                                  (int(direct), user["id"])).lastrowid
            for k, v in values.items():
                if v:
                    self._contribute(user, rid, k, "", v)
            self._changed()
        return rid, direct

    def review(self, admin, cid, approve):
        with self.lock:
            c = self.db.execute("SELECT * FROM contributions WHERE id = ? AND status = 'pending'",
                                (cid,)).fetchone()
            if c is None:
                raise EditError("That contribution was already reviewed.")
            if approve:
                self.db.execute(f"UPDATE records SET {c['field']} = ? WHERE id = ?",
                                (c["new_value"], c["record_id"]))
                self.db.execute("UPDATE records SET visible = 1 WHERE id = ?", (c["record_id"],))
            self.db.execute("UPDATE contributions SET status = ?, reviewed_by = ? WHERE id = ?",
                            ("applied" if approve else "rejected", admin["id"], cid))
            self._changed()

    def ban_user(self, admin, uid):
        """Ignore a user: block login, drop pending edits and roll back everything they applied."""
        with self.lock:
            if uid == admin["id"]:
                raise EditError("You cannot remove your own edits.")
            self.db.execute("UPDATE users SET banned = 1 WHERE id = ?", (uid,))
            self.db.execute("DELETE FROM sessions WHERE user_id = ?", (uid,))
            self.db.execute("UPDATE contributions SET status = 'rejected', reviewed_by = ? "
                            "WHERE user_id = ? AND status = 'pending'", (admin["id"], uid))
            cells = self.db.execute("SELECT DISTINCT record_id, field FROM contributions "
                                    "WHERE user_id = ? AND status = 'applied'", (uid,)).fetchall()
            self.db.execute("UPDATE contributions SET status = 'reverted', reviewed_by = ? "
                            "WHERE user_id = ? AND status = 'applied'", (admin["id"], uid))
            for c in cells:
                rid, field = c["record_id"], c["field"]
                last = self.db.execute(
                    "SELECT new_value FROM contributions WHERE record_id = ? AND field = ? "
                    "AND status = 'applied' ORDER BY id DESC LIMIT 1", (rid, field)).fetchone()
                first = self.db.execute(
                    "SELECT old_value FROM contributions WHERE record_id = ? AND field = ? "
                    "ORDER BY id LIMIT 1", (rid, field)).fetchone()
                self.db.execute(f"UPDATE records SET {field} = ? WHERE id = ?",
                                ((last or first)[0], rid))
            self.db.execute("UPDATE records SET visible = 0 WHERE created_by = ?", (uid,))
            self._changed()

    def unban_user(self, uid):
        with self.lock:
            self.db.execute("UPDATE users SET banned = 0 WHERE id = ?", (uid,))
            self._changed()
