"""Wiki-style database editing prototype: a tiny web app on SQLite, standard library only."""
import html
import json
import os
import secrets
import sys
import urllib.parse
import urllib.request
from http import cookies
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from store import FIELDS, EditError, Store

OAUTH = {
    "github": {
        "label": "GitHub",
        "authorize": "https://github.com/login/oauth/authorize",
        "token": "https://github.com/login/oauth/access_token",
        "userinfo": "https://api.github.com/user",
        "scope": "read:user",
        "id_key": "id",
        "name_key": "login",
    },
    "google": {
        "label": "Google",
        "authorize": "https://accounts.google.com/o/oauth2/v2/auth",
        "token": "https://oauth2.googleapis.com/token",
        "userinfo": "https://openidconnect.googleapis.com/v1/userinfo",
        "scope": "openid profile",
        "id_key": "sub",
        "name_key": "name",
    },
}

CSS = """
body{font:16px/1.5 system-ui,sans-serif;margin:0 auto;max-width:56rem;padding:1rem;color:#222}
nav{display:flex;flex-wrap:wrap;gap:.75rem;align-items:center;border-bottom:1px solid #ccc;padding-bottom:.5rem}
nav form{display:inline;margin:0}.grow{flex:1}
table{border-collapse:collapse;width:100%}th,td{border:1px solid #ddd;padding:.4rem;text-align:left;vertical-align:top}
.scroll{overflow-x:auto}.blank{color:#b00;font-style:italic}
label{display:block;margin:.75rem 0 .2rem;font-weight:600}
input[type=text]{width:100%;box-sizing:border-box;font:inherit;padding:.5rem}
button,.button{font:inherit;padding:.5rem 1rem;cursor:pointer}
.msg{background:#eef6ff;border:1px solid #9bc;padding:.5rem;margin:.75rem 0}.err{background:#fee;border-color:#c99}
"""

LIVE_JS = """
var seen=null,es=new EventSource('/events');
es.onmessage=function(e){if(seen!==null&&e.data!==seen){
if(document.querySelector('form[data-edit]')){document.getElementById('live').hidden=false}
else{location.reload()}}seen=e.data};
"""


def esc(value):
    return html.escape(str(value), quote=True)


class Config:
    def __init__(self, env=os.environ):
        self.base_url = env.get("WIKI_BASE_URL", "http://127.0.0.1:8000").rstrip("/")
        self.demo = env.get("WIKI_DEMO") == "1"
        # Admins are listed as "provider:id", e.g. WIKI_ADMINS="github:12345,demo:admin".
        self.admins = {a.strip().lower() for a in env.get("WIKI_ADMINS", "").split(",") if a.strip()}
        self.oauth = {p: (env.get(f"{p.upper()}_CLIENT_ID"), env.get(f"{p.upper()}_CLIENT_SECRET"))
                      for p in OAUTH if env.get(f"{p.upper()}_CLIENT_ID")
                      and env.get(f"{p.upper()}_CLIENT_SECRET")}

    def is_admin(self, provider, provider_id):
        return f"{provider}:{provider_id}".lower() in self.admins


def oauth_fetch(url, data=None, token=None):
    """Small JSON HTTP helper for the OAuth round trip."""
    headers = {"Accept": "application/json", "User-Agent": "wiki-db-prototype"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    body = urllib.parse.urlencode(data).encode() if data else None
    with urllib.request.urlopen(urllib.request.Request(url, body, headers), timeout=10) as resp:
        return json.load(resp)


class Handler(BaseHTTPRequestHandler):
    store: Store
    config: Config
    states: set

    def log_message(self, *args):
        pass

    # -- plumbing ------------------------------------------------------
    def _session(self):
        jar = cookies.SimpleCookie(self.headers.get("Cookie", ""))
        sid = jar["sid"].value if "sid" in jar else ""
        return (self.store.session(sid) if sid else None), sid

    def _send(self, body, status=200, headers=(), ctype="text/html; charset=utf-8"):
        data = body.encode()
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        for k, v in headers:
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def _redirect(self, where, headers=()):
        self.send_response(303)
        self.send_header("Location", where)
        self.send_header("Content-Length", "0")
        for k, v in headers:
            self.send_header(k, v)
        self.end_headers()

    def _form(self):
        length = int(self.headers.get("Content-Length") or 0)
        parsed = urllib.parse.parse_qs(self.rfile.read(length).decode(), keep_blank_values=True)
        return {k: v[0] for k, v in parsed.items()}

    def _page(self, title, body, user=None, csrf="", note=None, error=False):
        who = ""
        if user:
            admin = f'<a href="/admin">Admin</a>' if user["is_admin"] else ""
            who = (f'{admin}<span>Hi, {esc(user["name"])}</span><form method="post" action="/logout">'
                   f'<input type="hidden" name="csrf" value="{esc(csrf)}"><button>Log out</button></form>')
        else:
            who = '<a class="button" href="/login">Log in to edit</a>'
        flash = f'<p class="msg{" err" if error else ""}">{esc(note)}</p>' if note else ""
        live = ('<p class="msg" id="live" hidden>Someone changed this data. '
                '<a href="">Reload</a> to see it.</p>')
        return (f'<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" '
                f'content="width=device-width,initial-scale=1"><title>{esc(title)}</title>'
                f'<style>{CSS}</style><nav><a href="/"><strong>Shared directory</strong></a>'
                f'<span class="grow"></span>{who}</nav>{flash}{live}{body}<script>{LIVE_JS}</script></html>')

    def _require(self, admin=False):
        """Return (user, csrf), or None after sending the right redirect/error."""
        sess, _ = self._session()
        if not sess:
            self._redirect("/login")
        elif admin and not sess[0]["is_admin"]:
            self._send("Admins only.", 403, ctype="text/plain")
        else:
            return sess
        return None

    def _post_guard(self, admin=False):
        """Check login, admin role and CSRF token; return (user, csrf, form) or None."""
        sess = self._require(admin)
        if not sess:
            return None
        form = self._form()
        if not secrets.compare_digest(form.get("csrf", ""), sess[1]):
            self._send("Bad CSRF token.", 403, ctype="text/plain")
            return None
        return sess[0], sess[1], form

    # -- routing -------------------------------------------------------
    def do_GET(self):
        url = urllib.parse.urlparse(self.path)
        parts = url.path.strip("/").split("/")
        query = urllib.parse.parse_qs(url.query)
        note = query.get("note", [None])[0]
        if url.path == "/":
            self.index(note)
        elif url.path == "/events":
            self.events()
        elif url.path == "/login":
            self.login_page()
        elif parts[0] == "login" and len(parts) == 2 and parts[1] in self.config.oauth:
            self.oauth_start(parts[1])
        elif parts[0] == "callback" and len(parts) == 2 and parts[1] in self.config.oauth:
            self.oauth_callback(parts[1], query)
        elif url.path == "/new":
            self.form_page(None)
        elif parts[0] == "record" and len(parts) == 2 and parts[1].isdigit():
            self.form_page(int(parts[1]))
        elif url.path == "/admin":
            self.admin_page(note)
        else:
            self._send("Not found.", 404, ctype="text/plain")

    def do_POST(self):
        path = self.path.split("?")[0]
        parts = path.strip("/").split("/")
        if path == "/login/demo":
            self.demo_login()
        elif path == "/logout":
            _, sid = self._session()
            self.store.logout(sid)
            self._redirect("/", [("Set-Cookie", "sid=; Path=/; Max-Age=0")])
        elif path == "/new":
            self.save(None)
        elif parts[0] == "record" and len(parts) == 2 and parts[1].isdigit():
            self.save(int(parts[1]))
        elif parts[0] == "admin" and len(parts) == 2:
            self.admin_action(parts[1])
        else:
            self._send("Not found.", 404, ctype="text/plain")

    # -- pages ---------------------------------------------------------
    def index(self, note):
        sess, _ = self._session()
        user, csrf = sess if sess else (None, "")
        rows = ""
        for rec in self.store.records():
            cells = "".join(f'<td>{esc(rec[k]) or "<span class=blank>blank – add it!</span>"}</td>'
                            for k, _ in FIELDS)
            rows += f'<tr>{cells}<td><a class="button" href="/record/{rec["id"]}">Edit</a></td></tr>'
        head = "".join(f"<th>{esc(label)}</th>" for _, label in FIELDS)
        mode = ("Edits are reviewed by an admin before they appear."
                if self.store.review_required() else "Edits appear immediately.")
        pending = self.store.pending_for(user["id"]) if user else 0
        mine = f" You have {pending} edit(s) waiting for review." if pending else ""
        body = (f'<h1>Shared directory</h1><p>{mode}{mine}</p><div class="scroll"><table><tr>{head}'
                f'<th></th></tr>{rows}</table></div><p><a class="button" href="/new">Add a new entry</a></p>')
        self._send(self._page("Shared directory", body, user, csrf, note))

    def login_page(self):
        links = "".join(f'<p><a class="button" href="/login/{p}">Continue with {OAUTH[p]["label"]}</a></p>'
                        for p in self.config.oauth)
        demo = ""
        if self.config.demo:
            demo = ('<h2>Demo login</h2><p>No password: type any name. Use the name '
                    '<strong>admin</strong> to try the admin tools.</p>'
                    '<form method="post" action="/login/demo"><label for="n">Your name</label>'
                    '<input id="n" type="text" name="name" required> <p><button>Log in</button></p></form>')
        if not links and not demo:
            links = "<p>No login method is configured. See the README.</p>"
        self._send(self._page("Log in", f"<h1>Log in</h1>{links}{demo}"))

    def form_page(self, rid):
        sess = self._require()
        if not sess:
            return
        user, csrf = sess
        rec = self.store.record(rid) if rid else None
        if rid and rec is None:
            return self._send("Not found.", 404, ctype="text/plain")
        fields = ""
        for k, label in FIELDS:
            val = rec[k] if rec else ""
            fields += (f'<label for="f_{k}">{esc(label)}</label><input id="f_{k}" type="text" name="{k}" '
                       f'value="{esc(val)}" maxlength="500"><input type="hidden" name="base_{k}" '
                       f'value="{esc(val)}">')
        title = "Edit entry" if rec else "Add a new entry"
        action = f"/record/{rid}" if rid else "/new"
        body = (f'<h1>{title}</h1><form method="post" action="{action}" data-edit>'
                f'<input type="hidden" name="csrf" value="{esc(csrf)}">{fields}'
                f'<p><button>Save</button> <a href="/">Cancel</a></p></form>')
        self._send(self._page(title, body, user, csrf))

    def save(self, rid):
        guard = self._post_guard()
        if not guard:
            return
        user, csrf, form = guard
        try:
            if rid:
                base = {k: form.get(f"base_{k}", "") for k, _ in FIELDS}
                applied, held = self.store.edit_record(user, rid, form, base)
            else:
                _, direct = self.store.add_record(user, form)
                applied, held = (1, 0) if direct else (0, 1)
        except EditError as exc:
            return self._send(self._page("Error", '<p><a href="/">Back</a></p>', user, csrf,
                                         str(exc), error=True), 409)
        note = ("Thanks! Your change is waiting for an admin to review it." if held
                else "Saved." if applied else "Nothing changed.")
        self._redirect("/?note=" + urllib.parse.quote(note))

    def admin_page(self, note):
        sess = self._require(admin=True)
        if not sess:
            return
        user, csrf = sess
        token = f'<input type="hidden" name="csrf" value="{esc(csrf)}">'
        on = self.store.review_required()
        gate = (f'<form method="post" action="/admin/gate">{token}<p>Admin review is '
                f'<strong>{"ON" if on else "OFF"}</strong>. <button name="on" value="{0 if on else 1}">'
                f'Turn {"off" if on else "on"}</button></p></form>')
        queue = ""
        for c in self.store.contributions("pending"):
            queue += (f'<tr><td>{esc(c["user_name"])}</td><td>{esc(c["record_name"] or "(new entry)")}</td>'
                      f'<td>{esc(dict(FIELDS)[c["field"]])}</td><td>{esc(c["old_value"])}</td>'
                      f'<td>{esc(c["new_value"])}</td><td><form method="post" action="/admin/review">{token}'
                      f'<input type="hidden" name="id" value="{c["id"]}">'
                      f'<button name="approve" value="1">Approve</button> '
                      f'<button name="approve" value="0">Reject</button></form></td></tr>')
        queue = (f'<div class="scroll"><table><tr><th>Who</th><th>Entry</th><th>Field</th><th>Was</th>'
                 f'<th>Proposed</th><th></th></tr>{queue}</table></div>') if queue else "<p>Nothing to review.</p>"
        people = ""
        for u in self.store.users():
            if u["banned"]:
                action = "unban"
            elif u["id"] != user["id"]:
                action = "ban"
            else:
                action = None
            button = (f'<form method="post" action="/admin/user">{token}<input type="hidden" name="id" '
                      f'value="{u["id"]}"><button name="action" value="{action}">'
                      f'{"Restore user" if action == "unban" else "Remove all edits and block"}</button>'
                      f'</form>') if action else ""
            people += (f'<tr><td>{esc(u["name"])}</td><td>{esc(u["provider"])}</td>'
                       f'<td>{"admin" if u["is_admin"] else "blocked" if u["banned"] else "member"}</td>'
                       f'<td>{u["edits"]}</td><td>{button}</td></tr>')
        hist = "".join(f'<tr><td>{esc(c["user_name"])}</td><td>{esc(c["record_name"])}</td>'
                       f'<td>{esc(c["field"])}</td><td>{esc(c["old_value"])} → {esc(c["new_value"])}</td>'
                       f'<td>{esc(c["status"])}</td></tr>' for c in self.store.contributions())
        body = (f'<h1>Admin</h1>{gate}<h2>Waiting for review</h2>{queue}<h2>People</h2><div class="scroll">'
                f'<table><tr><th>Name</th><th>Login</th><th>Role</th><th>Edits</th><th></th></tr>{people}'
                f'</table></div><h2>Change history</h2><div class="scroll"><table><tr><th>Who</th>'
                f'<th>Entry</th><th>Field</th><th>Change</th><th>Status</th></tr>{hist}</table></div>')
        self._send(self._page("Admin", body, user, csrf, note))

    def admin_action(self, action):
        guard = self._post_guard(admin=True)
        if not guard:
            return
        user, _, form = guard
        note = "Done."
        try:
            if action == "gate":
                self.store.set_review_required(form.get("on") == "1")
            elif action == "review":
                self.store.review(user, int(form["id"]), form.get("approve") == "1")
            elif action == "user":
                if form.get("action") == "ban":
                    self.store.ban_user(user, int(form["id"]))
                else:
                    self.store.unban_user(int(form["id"]))
            else:
                return self._send("Not found.", 404, ctype="text/plain")
        except (EditError, KeyError, ValueError) as exc:
            note = str(exc)
        self._redirect("/admin?note=" + urllib.parse.quote(note))

    # -- live updates ----------------------------------------------------
    def events(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        seen = None
        try:
            while True:
                current = self.store.version if seen is None else self.store.wait_for_change(seen, 15)
                self.wfile.write(f"data: {current}\n\n".encode() if current != seen else b": ping\n\n")
                self.wfile.flush()
                seen = current
        except (BrokenPipeError, ConnectionResetError):
            pass

    # -- login ---------------------------------------------------------
    def _start_session(self, result):
        if result is None:
            return self._send("This account has been blocked by an admin.", 403, ctype="text/plain")
        _, sid, _ = result
        self._redirect("/", [("Set-Cookie", f"sid={sid}; Path=/; HttpOnly; SameSite=Lax")])

    def demo_login(self):
        if not self.config.demo:
            return self._send("Demo login is disabled.", 403, ctype="text/plain")
        name = " ".join(self._form().get("name", "").split())[:40]
        if not name:
            return self._redirect("/login")
        pid = name.lower()
        self._start_session(self.store.login("demo", pid, name, self.config.is_admin("demo", pid)))

    def oauth_start(self, provider):
        state = secrets.token_urlsafe(16)
        self.states.add(state)
        spec = OAUTH[provider]
        query = urllib.parse.urlencode({
            "client_id": self.config.oauth[provider][0], "state": state, "scope": spec["scope"],
            "redirect_uri": f"{self.config.base_url}/callback/{provider}", "response_type": "code"})
        self._redirect(f'{spec["authorize"]}?{query}')

    def oauth_callback(self, provider, query):
        state = query.get("state", [""])[0]
        if state not in self.states or "code" not in query:
            return self._send("Login failed: bad state.", 400, ctype="text/plain")
        self.states.discard(state)
        spec, (client_id, secret) = OAUTH[provider], self.config.oauth[provider]
        try:
            tok = oauth_fetch(spec["token"], {
                "client_id": client_id, "client_secret": secret, "code": query["code"][0],
                "grant_type": "authorization_code",
                "redirect_uri": f"{self.config.base_url}/callback/{provider}"})
            info = oauth_fetch(spec["userinfo"], token=tok["access_token"])
            pid, name = str(info[spec["id_key"]]), str(info.get(spec["name_key"]) or info[spec["id_key"]])
        except (OSError, KeyError, ValueError):
            return self._send("Login failed: the provider did not confirm your identity.", 502,
                              ctype="text/plain")
        self._start_session(self.store.login(provider, pid, name, self.config.is_admin(provider, pid)))


def make_server(store, config, port=8000, host="127.0.0.1"):
    handler = type("BoundHandler", (Handler,), {"store": store, "config": config, "states": set()})
    server = ThreadingHTTPServer((host, port), handler)
    server.daemon_threads = True
    return server


def main():
    config = Config()
    store = Store(os.environ.get("WIKI_DB", "wiki.db"))
    port = int(os.environ.get("PORT", "8000"))
    server = make_server(store, config, port)
    print(f"Wiki DB prototype running at http://127.0.0.1:{port}  (Ctrl+C to stop)", file=sys.stderr)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
