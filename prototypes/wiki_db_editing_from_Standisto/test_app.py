from unittest import mock
import pytest
from app import create_app


@pytest.fixture
def app(tmp_path):
    return create_app({"DATABASE_PATH": str(tmp_path / "t.db"), "DEV_LOGIN": True, "SECRET_KEY": "t",
                       "ADMIN_GITHUB_LOGINS": {"boss"}})


def login(c, name, admin=False):
    c.post("/dev-login", data={"name": name, **({"admin": "1"} if admin else {})})


def test_full_flow(app):
    admin, alice = app.test_client(), app.test_client()
    login(admin, "Admin", admin=True); login(alice, "Alice")
    # anonymous cannot edit
    assert app.test_client().get("/entry/new").status_code == 302
    # gate ON: new entry goes to queue
    alice.post("/entry/new", data={"name": "Coop A", "website": "a.org", "location": "X", "description": "d"})
    assert b"Coop A" not in alice.get("/").data
    assert alice.get("/admin").status_code == 403
    assert b"Coop A" in admin.get("/admin").data
    admin.post("/admin/proposal/1/approve")
    assert b"Coop A" in alice.get("/").data and b"Alice" in alice.get("/").data
    # edit existing, reject
    alice.post("/entry/1/edit", data={"name": "Coop A spam", "website": "", "location": "", "description": ""})
    admin.post("/admin/proposal/2/reject")
    assert b"spam" not in alice.get("/").data
    assert b"rejected" in alice.get("/entry/1/history").data
    # gate OFF: edit applies immediately
    admin.post("/admin/gate", data={"on": "0"})
    alice.post("/entry/1/edit", data={"name": "Coop A+", "website": "a.org", "location": "Y", "description": "d"})
    assert b"Coop A+" in alice.get("/").data
    # ban: pending edits rejected, further edits blocked
    admin.post("/admin/gate", data={"on": "1"})
    alice.post("/entry/new", data={"name": "Junk", "website": "", "location": "", "description": ""})
    admin.post("/admin/user/2/ban")
    assert b"Junk" not in admin.get("/admin").data
    assert alice.post("/entry/new", data={"name": "x"}).status_code == 403


def test_github_oauth_callback(app, monkeypatch):
    monkeypatch.setenv("GITHUB_CLIENT_ID", "id"); monkeypatch.setenv("GITHUB_CLIENT_SECRET", "s")
    c = app.test_client()
    r = c.get("/login/github")
    assert r.location.startswith("https://github.com/login/oauth/authorize")
    with c.session_transaction() as s:
        state = s["oauth_state"]
    tok = mock.Mock(json=lambda: {"access_token": "T"})
    user = mock.Mock(json=lambda: {"id": 7, "login": "boss", "name": "Boss", "email": "b@x"})
    with mock.patch("app.requests.post", return_value=tok), mock.patch("app.requests.get", return_value=user):
        c.get(f"/auth/github/callback?code=abc&state={state}")
    assert c.get("/admin").status_code == 200  # boss is admin via ADMIN_GITHUB_LOGINS


def test_bad_state_rejected(app, monkeypatch):
    monkeypatch.setenv("GITHUB_CLIENT_ID", "id")
    assert app.test_client().get("/auth/github/callback?code=x&state=nope").status_code == 400


def test_dev_login_disabled_by_default(tmp_path):
    a = create_app({"DATABASE_PATH": str(tmp_path / "x.db"), "DEV_LOGIN": False})
    assert a.test_client().post("/dev-login", data={"name": "a"}).status_code == 404


def test_google_oauth_callback(app, monkeypatch):
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "gid"); monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "s")
    c = app.test_client()
    r = c.get("/login/google")
    assert r.location.startswith("https://accounts.google.com/o/oauth2/v2/auth")
    with c.session_transaction() as s:
        state = s["oauth_state"]
    tok = mock.Mock(json=lambda: {"access_token": "T"})
    info = mock.Mock(json=lambda: {"sub": "g1", "name": "Gina", "email": "gina@x"})
    with mock.patch("app.requests.post", return_value=tok), mock.patch("app.requests.get", return_value=info):
        c.get(f"/auth/google/callback?code=abc&state={state}")
    c.post("/entry/new", data={"name": "G coop", "website": "", "location": "", "description": ""})
    assert c.get("/admin").status_code == 403  # normal contributor
    assert b"G coop" not in c.get("/").data


def test_blank_fields_can_be_filled(app):
    a = app.test_client(); login(a, "Admin", admin=True)
    a.post("/entry/new", data={"name": "Coop B", "website": "", "location": "", "description": ""})
    u = app.test_client(); login(u, "Bob")
    u.post("/entry/1/edit", data={"name": "Coop B", "website": "b.org", "location": "", "description": ""})
    a.post("/admin/proposal/2/approve")
    assert b"b.org" in u.get("/").data
