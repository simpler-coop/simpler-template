import http.client
import threading
import unittest
import urllib.parse

import app
from store import EditError, Store

ENV = {"WIKI_DEMO": "1", "WIKI_ADMINS": "demo:admin"}


class Client:
    """Minimal browser stand-in: keeps the session cookie, never follows redirects."""

    def __init__(self, port):
        self.port, self.sid = port, None

    def request(self, method, path, form=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        headers = {"Cookie": f"sid={self.sid}"} if self.sid else {}
        body = urllib.parse.urlencode(form).encode() if form is not None else None
        if body:
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        conn.request(method, path, body, headers)
        resp = conn.getresponse()
        text = resp.read().decode()
        cookie = resp.getheader("Set-Cookie") or ""
        if cookie.startswith("sid="):
            self.sid = cookie.split(";")[0][4:] or None
        conn.close()
        return resp.status, text, resp.getheader("Location")

    def csrf(self):
        page = self.request("GET", "/new")[1]
        return page.split('name="csrf" value="')[1].split('"')[0]

    def login(self, name):
        status, _, _ = self.request("POST", "/login/demo", {"name": name})
        assert status == 303 and self.sid
        return self

    def post(self, path, form):
        return self.request("POST", path, dict(form, csrf=self.csrf()))


class AppTest(unittest.TestCase):
    def setUp(self):
        self.store = Store()
        self.server = app.make_server(self.store, app.Config(ENV), port=0)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.store.db.close)
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def client(self, name=None):
        c = Client(self.port)
        return c.login(name) if name else c

    def cell(self, rid, field):
        return self.store.record(rid)[field]

    # 1. Database foundation
    def test_data_lives_in_sqlite_database(self):
        tables = {r[0] for r in self.store.db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertTrue({"records", "contributions", "users"} <= tables)
        self.assertEqual(self.store.records()[0]["name"], "Simpler Co-op")

    # 2. Individual accountability
    def test_every_change_is_attributed_to_an_account(self):
        self.store.set_review_required(False)
        alice, bob = self.client("Alice"), self.client("Bob")
        alice.post("/record/2", {"contact": "a@x.org", "base_contact": ""})
        bob.post("/record/2", {"name": "Bob's Name", "base_name": "Example Mutual Aid Network"})
        who = {(c["field"], c["user_name"]) for c in self.store.contributions()}
        self.assertEqual(who, {("contact", "Alice"), ("name", "Bob")})

    def test_anonymous_visitors_can_read_but_not_edit(self):
        anon = self.client()
        self.assertEqual(anon.request("GET", "/")[0], 200)
        self.assertEqual(anon.request("GET", "/record/1"), (303, "", "/login"))
        self.assertEqual(anon.request("POST", "/record/1", {"name": "x"})[0], 303)
        self.assertEqual(self.cell(1, "name"), "Simpler Co-op")

    # 3. Submit new entries (fill blank fields)
    def test_user_can_fill_blank_field_and_add_entry(self):
        self.store.set_review_required(False)
        c = self.client("Carol")
        c.post("/record/1", {"contact": "hello@example.org", "base_contact": ""})
        self.assertEqual(self.cell(1, "contact"), "hello@example.org")
        c.post("/new", {"name": "New Co-op", "website": "https://new.example"})
        self.assertEqual(self.store.records()[-1]["name"], "New Co-op")

    # 4. Edit existing entries
    def test_user_can_edit_existing_value(self):
        self.store.set_review_required(False)
        self.client("Dan").post("/record/1", {"website": "https://simpler.coop",
                                              "base_website": "https://github.com/simpler-coop"})
        self.assertEqual(self.cell(1, "website"), "https://simpler.coop")

    def test_stale_edit_is_refused(self):
        self.store.set_review_required(False)
        a, b = self.client("Ann"), self.client("Ben")
        a.post("/record/1", {"notes": "A", "base_notes": ""})
        status, text, _ = b.post("/record/1", {"notes": "B", "base_notes": ""})
        self.assertEqual(status, 409)
        self.assertIn("Someone else just changed", text)
        self.assertEqual(self.cell(1, "notes"), "A")

    # 5. Admin review gate, switchable
    def test_review_gate_holds_edits_until_admin_approves(self):
        self.assertTrue(self.store.review_required())
        user, admin = self.client("Eve"), self.client("admin")
        user.post("/record/1", {"contact": "eve@x.org", "base_contact": ""})
        self.assertEqual(self.cell(1, "contact"), "")
        pending = self.store.contributions("pending")
        self.assertEqual(len(pending), 1)
        self.assertIn("eve@x.org", admin.request("GET", "/admin")[1])
        admin.post("/admin/review", {"id": pending[0]["id"], "approve": "1"})
        self.assertEqual(self.cell(1, "contact"), "eve@x.org")

    def test_admin_can_reject(self):
        self.client("Eve").post("/record/1", {"contact": "spam", "base_contact": ""})
        admin = self.client("admin")
        admin.post("/admin/review", {"id": self.store.contributions("pending")[0]["id"], "approve": "0"})
        self.assertEqual(self.cell(1, "contact"), "")
        self.assertEqual(self.store.contributions()[0]["status"], "rejected")

    def test_new_entry_is_hidden_until_approved(self):
        self.client("Eve").post("/new", {"name": "Pending Org"})
        self.assertNotIn("Pending Org", [r["name"] for r in self.store.records()])
        self.client("admin").post("/admin/review",
                                  {"id": self.store.contributions("pending")[0]["id"], "approve": "1"})
        self.assertIn("Pending Org", [r["name"] for r in self.store.records()])

    def test_admin_can_toggle_gate_and_non_admin_cannot(self):
        admin, user = self.client("admin"), self.client("Fay")
        admin.post("/admin/gate", {"on": "0"})
        self.assertFalse(self.store.review_required())
        user.post("/record/1", {"contact": "fay@x.org", "base_contact": ""})
        self.assertEqual(self.cell(1, "contact"), "fay@x.org")
        self.assertEqual(user.post("/admin/gate", {"on": "1"})[0], 403)
        self.assertEqual(user.request("GET", "/admin")[0], 403)
        self.assertFalse(self.store.review_required())
        admin.post("/admin/gate", {"on": "1"})
        self.assertTrue(self.store.review_required())

    def test_csrf_token_required(self):
        c = self.client("admin")
        self.assertEqual(c.request("POST", "/admin/gate", {"on": "0"})[0], 403)
        self.assertTrue(self.store.review_required())

    # 6. Simple access
    def test_login_needs_only_a_name_in_demo_and_oauth_is_offered_when_configured(self):
        self.assertIn("Demo login", self.client().request("GET", "/login")[1])
        cfg = app.Config({"GITHUB_CLIENT_ID": "id", "GITHUB_CLIENT_SECRET": "s", "WIKI_ADMINS": "github:7"})
        self.assertEqual(set(cfg.oauth), {"github"})
        self.assertTrue(cfg.is_admin("github", 7))
        self.assertFalse(app.Config({}).demo)

    def test_oauth_callback_creates_account_keyed_by_provider_id(self):
        self.server.RequestHandlerClass.config = app.Config(
            {"GITHUB_CLIENT_ID": "id", "GITHUB_CLIENT_SECRET": "s", "WIKI_ADMINS": "github:42"})
        real = app.oauth_fetch
        app.oauth_fetch = lambda url, data=None, token=None: (
            {"access_token": "t"} if data else {"id": 42, "login": "octo"})
        self.addCleanup(setattr, app, "oauth_fetch", real)
        c = self.client()
        status, _, loc = c.request("GET", "/login/github")
        self.assertIn("github.com/login/oauth/authorize", loc)
        state = urllib.parse.parse_qs(urllib.parse.urlparse(loc).query)["state"][0]
        self.assertEqual(c.request("GET", "/callback/github?code=c&state=bad")[0], 400)
        self.assertEqual(c.request("GET", f"/callback/github?code=c&state={state}")[0], 303)
        user = self.store.session(c.sid)[0]
        self.assertEqual((user["provider"], user["provider_id"], user["is_admin"]), ("github", "42", 1))

    # 7. Easy UX
    def test_pages_are_plain_mobile_friendly_forms(self):
        c = self.client("Gus")
        page = c.request("GET", "/record/1")[1]
        self.assertIn("width=device-width", page)
        for label in ("Name", "Website", "Contact", "Notes"):
            self.assertIn(f">{label}</label>", page)
        self.assertIn("blank", c.request("GET", "/")[1])

    def test_confirmation_message_tells_user_when_review_pending(self):
        status, _, loc = self.client("Hal").post("/record/1", {"contact": "h@x.org", "base_contact": ""})
        self.assertEqual(status, 303)
        self.assertIn("review", urllib.parse.unquote(loc))

    def test_html_is_escaped(self):
        self.store.set_review_required(False)
        c = self.client("Ivy")
        c.post("/record/1", {"notes": "<script>alert(1)</script>", "base_notes": ""})
        page = c.request("GET", "/")[1]
        self.assertNotIn("<script>alert(1)", page)
        self.assertIn("&lt;script&gt;", page)

    # Nice to have: remove all edits of one user
    def test_admin_can_remove_all_edits_of_a_spammer(self):
        self.store.set_review_required(False)
        good, spam, admin = self.client("Good"), self.client("Spammer"), self.client("admin")
        good.post("/record/1", {"contact": "good@x.org", "base_contact": ""})
        spam.post("/record/1", {"contact": "buy pills", "base_contact": "good@x.org"})
        spam.post("/record/2", {"notes": "spam", "base_notes": "Add a website and a contact!"})
        spam.post("/new", {"name": "Spam Org"})
        spam_id = [u["id"] for u in self.store.users() if u["name"] == "Spammer"][0]
        admin.post("/admin/user", {"id": spam_id, "action": "ban"})
        self.assertEqual(self.cell(1, "contact"), "good@x.org")
        self.assertEqual(self.cell(2, "notes"), "Add a website and a contact!")
        self.assertNotIn("Spam Org", [r["name"] for r in self.store.records()])
        self.assertEqual(spam.request("GET", "/record/1")[0], 303)  # session is gone
        self.assertIsNone(self.store.login("demo", "spammer", "Spammer", False))
        admin.post("/admin/user", {"id": spam_id, "action": "unban"})
        self.assertIsNotNone(self.store.login("demo", "spammer", "Spammer", False))

    def test_admin_cannot_ban_self(self):
        admin = self.store.login("demo", "admin", "admin", True)[0]
        with self.assertRaises(EditError):
            self.store.ban_user(admin, admin["id"])

    # Nice to have: live co-editing
    def test_live_stream_announces_changes(self):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        conn.request("GET", "/events")
        resp = conn.getresponse()
        first = resp.fp.readline().decode().strip()
        resp.fp.readline()
        self.assertTrue(first.startswith("data: "))
        self.client("admin").post("/record/1", {"notes": "live", "base_notes": ""})
        second = resp.fp.readline().decode().strip()
        self.assertTrue(second.startswith("data: "))
        self.assertNotEqual(first, second)
        conn.close()


if __name__ == "__main__":
    unittest.main()
