"""Local, standard-library SQLite contribution-review prototype."""
import argparse
import hashlib
import html
import json
import os
import secrets
import sqlite3
import time
import urllib.parse
import urllib.request
from http.cookies import SimpleCookie
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path

FIELDS = ('name', 'description', 'website')


class Store:
    def __init__(self, path):
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript('''
        PRAGMA foreign_keys=ON;
        CREATE TABLE IF NOT EXISTS users(id TEXT PRIMARY KEY, login TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS records(id INTEGER PRIMARY KEY, name TEXT NOT NULL DEFAULT '',
          description TEXT NOT NULL DEFAULT '', website TEXT NOT NULL DEFAULT '');
        CREATE TABLE IF NOT EXISTS changes(id INTEGER PRIMARY KEY, record_id INTEGER NOT NULL REFERENCES records,
          field TEXT NOT NULL, old_value TEXT NOT NULL, new_value TEXT NOT NULL,
          author TEXT NOT NULL REFERENCES users, status TEXT NOT NULL,
          reviewer TEXT REFERENCES users, created TEXT DEFAULT CURRENT_TIMESTAMP);
        CREATE TABLE IF NOT EXISTS settings(id INTEGER PRIMARY KEY CHECK(id=1), review INTEGER NOT NULL);
        INSERT OR IGNORE INTO settings VALUES(1,1);
        INSERT OR IGNORE INTO records(id,name) VALUES(1,'Community resource');
        INSERT OR IGNORE INTO records(id) VALUES(2);
        ''')
        self.db.commit()

    def user(self, uid, login):
        with self.db:
            self.db.execute('INSERT INTO users VALUES(?,?) ON CONFLICT(id) DO UPDATE SET login=excluded.login', (uid, login))

    def gate(self):
        return bool(self.db.execute('SELECT review FROM settings').fetchone()[0])

    def set_gate(self, enabled, admin):
        if not admin:
            raise PermissionError('Administrator access required')
        with self.db:
            self.db.execute('UPDATE settings SET review=?', (int(enabled),))

    def propose(self, uid, rid, field, old, new):
        if field not in FIELDS or len(new) > 2000:
            raise ValueError('Invalid field or value exceeds 2,000 characters')
        with self.db:
            if not self.db.execute('SELECT 1 FROM users WHERE id=?', (uid,)).fetchone():
                raise PermissionError('Sign in first')
            row = self.db.execute('SELECT * FROM records WHERE id=?', (rid,)).fetchone()
            if not row or row[field] != old:
                raise ValueError('This field changed. Reload and submit again.')
            if old == new:
                raise ValueError('No change to save')
            status = 'pending' if self.gate() else 'approved'
            c = self.db.execute('INSERT INTO changes(record_id,field,old_value,new_value,author,status) VALUES(?,?,?,?,?,?)',
                                (rid, field, old, new, uid, status))
            if status == 'approved':
                self.db.execute('UPDATE records SET '+field+'=? WHERE id=?', (new, rid))
            return c.lastrowid

    def review(self, cid, uid, approve, admin):
        if not admin:
            raise PermissionError('Administrator access required')
        with self.db:
            c = self.db.execute('SELECT * FROM changes WHERE id=?', (cid,)).fetchone()
            if not c or c['status'] != 'pending':
                raise ValueError('This contribution is no longer pending')
            status = 'rejected'
            if approve:
                row = self.db.execute('SELECT * FROM records WHERE id=?', (c['record_id'],)).fetchone()
                if row[c['field']] != c['old_value']:
                    status = 'conflict'
                else:
                    self.db.execute('UPDATE records SET '+c['field']+'=? WHERE id=?', (c['new_value'], c['record_id']))
                    status = 'approved'
            self.db.execute('UPDATE changes SET status=?,reviewer=? WHERE id=?', (status, uid, cid))
            return status


def github_json(url, data=None, token=None):
    headers = {'Accept': 'application/json', 'User-Agent': 'wiki-sql-prototype'}
    if token:
        headers['Authorization'] = 'Bearer '+token
    request = urllib.request.Request(url, data=urllib.parse.urlencode(data).encode() if data else None, headers=headers)
    with urllib.request.urlopen(request, timeout=15) as response:
        return json.load(response)


CSS = '''body{font:16px system-ui;max-width:1000px;margin:36px auto;padding:0 20px;background:#f5f7fa;color:#17233b}
header,section,article{background:white;padding:22px;border-radius:12px;margin:18px 0;border:1px solid #dce2ea}
h1{margin:0}label{display:block;font-weight:600}input,textarea{box-sizing:border-box;width:100%;padding:10px;margin:6px 0;border:1px solid #9ca9ba;border-radius:5px}
button,.button{background:#2454ba;color:white;padding:10px 15px;border:0;border-radius:6px;cursor:pointer;display:inline-block;text-decoration:none}
form{margin:12px 0}.inline{display:inline-block;margin-right:10px}small{color:#536174}code{overflow-wrap:anywhere}.badge{font-weight:bold}table{width:100%;border-collapse:collapse}td,th{text-align:left;border-bottom:1px solid #ddd;padding:10px;overflow-wrap:anywhere}'''
E = lambda s: html.escape(str(s), quote=True)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass  # Do not log OAuth codes, cookies or personal data.

    def output(self, body, status=200, location=None, cookie=None):
        self.send_response(status)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('Content-Security-Policy', "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; base-uri 'none'; frame-ancestors 'none'")
        if location:
            self.send_header('Location', location)
        if cookie:
            self.send_header('Set-Cookie', cookie)
        self.end_headers()
        self.wfile.write(body.encode())

    def session(self):
        cookies = SimpleCookie()
        try:
            cookies.load(self.headers.get('Cookie', ''))
            sid = cookies['wiki_session'].value if 'wiki_session' in cookies else ''
        except Exception:
            sid = ''
        s = self.server.sessions.get(sid)
        if s and s['expires'] > time.time():
            return sid, s
        return '', None

    def new_session(self, uid=None):
        sid = secrets.token_urlsafe(32)
        s = {'uid': uid, 'csrf': secrets.token_urlsafe(32), 'expires': time.time()+3600}
        self.server.sessions[sid] = s
        for key in list(self.server.sessions):
            if self.server.sessions[key]['expires'] < time.time():
                del self.server.sessions[key]
        return sid, s

    def cookie(self, sid):
        return 'wiki_session='+sid+'; HttpOnly; SameSite=Lax; Path=/; Max-Age=3600'

    def form(self, s, action, inner):
        return '<form method="post" action="'+action+'"><input type="hidden" name="csrf" value="'+E(s['csrf'])+'">'+inner+'</form>'

    def valid_host(self):
        return self.headers.get('Host') == '127.0.0.1:'+str(self.server.server_port)

    def do_GET(self):
        if not self.valid_host():
            return self.output('Use the printed 127.0.0.1 URL.', 400)
        path = urllib.parse.urlsplit(self.path)
        sid, s = self.session()
        if path.path == '/login' and not self.server.demo:
            sid, s = self.new_session()
            s['state'] = secrets.token_urlsafe(32)
            s['verifier'] = secrets.token_urlsafe(48)
            import base64
            challenge = base64.urlsafe_b64encode(hashlib.sha256(s['verifier'].encode()).digest()).rstrip(b'=').decode()
            args = {'client_id':os.environ['GITHUB_CLIENT_ID'], 'redirect_uri':self.server.origin+'/callback',
                    'state':s['state'], 'code_challenge':challenge, 'code_challenge_method':'S256'}
            return self.output('', 303, 'https://github.com/login/oauth/authorize?'+urllib.parse.urlencode(args), self.cookie(sid))
        if path.path == '/callback' and not self.server.demo:
            query = urllib.parse.parse_qs(path.query)
            state = query.get('state', [''])[0]
            expected = s.pop('state', '') if s else ''
            if not expected or not secrets.compare_digest(state, expected):
                return self.output('Invalid or expired sign-in. Try again.', 400)
            try:
                token = github_json('https://github.com/login/oauth/access_token', {
                    'client_id':os.environ['GITHUB_CLIENT_ID'], 'client_secret':os.environ['GITHUB_CLIENT_SECRET'],
                    'code':query.get('code',[''])[0], 'redirect_uri':self.server.origin+'/callback', 'code_verifier':s.pop('verifier')})
                user = github_json('https://api.github.com/user', token=token['access_token'])
                uid = str(user['id'])
                self.server.store.user(uid, user['login'])
            except Exception:
                return self.output('Sign-in could not be completed. Try again.', 502)
            del self.server.sessions[sid]
            sid, s = self.new_session(uid)
            return self.output('', 303, '/', self.cookie(sid))
        if path.path != '/':
            return self.output('Not found', 404)
        cookie = None
        if not s:
            sid, s = self.new_session()
            cookie = self.cookie(sid)
        db = self.server.store
        uid = s['uid']
        admin = uid in self.server.admins
        body = '<header><h1>Community notebook</h1><p>Shared facts. Clear attribution. Review before publication.</p>'
        if self.server.demo:
            body += '<p class="badge">LOCAL DEMO — synthetic accounts, not real authentication</p>'
        if not uid:
            if self.server.demo:
                body += self.form(s, '/demo', '<button name="user" value="demo-editor">Try as contributor</button> <button name="user" value="demo-admin">Try as administrator</button>')
            else:
                body += '<a class="button" href="/login">Continue with GitHub</a>'
            return self.output('<!doctype html><html lang="en"><meta charset="utf-8"><title>Community notebook</title><style>'+CSS+'</style>'+body+'</header></html>', cookie=cookie)
        login = db.db.execute('SELECT login FROM users WHERE id=?',(uid,)).fetchone()[0]
        body += '<p>Signed in as '+E(login)+(' · Administrator' if admin else ' · Contributor')+'</p>'
        body += self.form(s, '/logout', '<button>Sign out</button>')+'</header>'
        body += '<section><h2>Published records</h2><p>'+('Edits wait for administrator approval.' if db.gate() else 'Edits publish immediately. History is still recorded.')+'</p>'
        for row in db.db.execute('SELECT * FROM records ORDER BY id'):
            body += '<article><h3>Record '+str(row['id'])+'</h3>'
            for field in FIELDS:
                inner = '<input type="hidden" name="record" value="'+str(row['id'])+'"><input type="hidden" name="field" value="'+field+'"><input type="hidden" name="old" value="'+E(row[field])+'">'
                inner += '<label>'+field.title()+'<textarea name="value" maxlength="2000" rows="2">'+E(row[field])+'</textarea></label><button>'+('Submit for review' if db.gate() else 'Save change')+'</button>'
                body += self.form(s, '/edit', inner)
            body += '</article>'
        body += '</section>'
        if admin:
            body += '<section><h2>Administrator controls</h2><p>Changing the gate affects new edits only. Existing pending edits still require a decision.</p>'
            body += self.form(s, '/gate', '<button name="enabled" value="'+('0' if db.gate() else '1')+'">'+('Turn review OFF' if db.gate() else 'Turn review ON')+'</button>')+'</section>'
        body += '<section><h2>Contribution history'+(' and review queue' if admin else '')+'</h2>'
        for c in db.db.execute('SELECT changes.*,users.login FROM changes JOIN users ON users.id=changes.author ORDER BY changes.id DESC'):
            body += '<article><p><b>'+E(c['status'])+'</b> · '+E(c['login'])+' · Record '+str(c['record_id'])+' / '+E(c['field'])+' · '+E(c['created'])+'</p><p>Before: <code>'+E(c['old_value'] or '(blank)')+'</code></p><p>After: <code>'+E(c['new_value'])+'</code></p>'
            if c['reviewer']:
                body += '<p>Reviewed by '+E(c['reviewer'])+'</p>'
            if admin and c['status']=='pending':
                body += self.form(s,'/review','<input type="hidden" name="change" value="'+str(c['id'])+'"><button name="decision" value="approve">Approve</button> <button name="decision" value="reject">Reject</button>')
            body += '</article>'
        body += '</section>'
        self.output('<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>Community notebook</title><style>'+CSS+'</style>'+body+'</html>', cookie=cookie)

    def do_POST(self):
        if not self.valid_host():
            return self.output('Invalid host',400)
        sid, s = self.session()
        try:
            length = int(self.headers.get('Content-Length','0'))
            if length <= 0 or length > 20000:
                raise ValueError('Invalid request size')
            data = urllib.parse.parse_qs(self.rfile.read(length).decode(), keep_blank_values=True)
            value = lambda key: data[key][0]
            if not s or not secrets.compare_digest(value('csrf'), s['csrf']):
                raise PermissionError('Expired form. Reload and try again.')
            uid = s['uid']
            db = self.server.store
            if self.path == '/demo' and self.server.demo:
                uid = value('user')
                if uid not in ('demo-editor','demo-admin'):
                    raise ValueError('Unknown demo account')
                db.user(uid, uid)
                del self.server.sessions[sid]
                sid, s = self.new_session(uid)
                return self.output('',303,'/',self.cookie(sid))
            if not uid:
                raise PermissionError('Sign in first')
            admin = uid in self.server.admins
            if self.path == '/logout':
                del self.server.sessions[sid]
                return self.output('',303,'/','wiki_session=; HttpOnly; SameSite=Lax; Path=/; Max-Age=0')
            elif self.path == '/edit':
                db.propose(uid,int(value('record')),value('field'),value('old'),value('value'))
            elif self.path == '/gate':
                if value('enabled') not in ('0','1'):
                    raise ValueError('Invalid gate setting')
                db.set_gate(value('enabled')=='1',admin)
            elif self.path == '/review':
                if value('decision') not in ('approve','reject'):
                    raise ValueError('Invalid decision')
                db.review(int(value('change')),uid,value('decision')=='approve',admin)
            else:
                return self.output('Not found',404)
            self.output('',303,'/')
        except PermissionError as error:
            self.output(E(error)+' <a href="/">Return</a>',403)
        except (ValueError, KeyError, UnicodeError) as error:
            self.output(E(error)+' <a href="/">Return and reload</a>',400)


def make_server(path, port=8000, demo=False, admins=()):
    server = HTTPServer(('127.0.0.1', port), Handler)
    server.store = Store(path)
    server.sessions = {}
    server.demo = demo
    server.admins = {'demo-admin'} if demo else set(admins)
    server.origin = 'http://127.0.0.1:'+str(server.server_port)
    return server


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--demo', action='store_true', help='Isolated local demonstration with synthetic identities')
    parser.add_argument('--port', type=int, default=8000)
    args = parser.parse_args()
    if not args.demo and not all(os.environ.get(k) for k in ('GITHUB_CLIENT_ID','GITHUB_CLIENT_SECRET','ADMIN_GITHUB_IDS')):
        parser.error('Set GITHUB_CLIENT_ID, GITHUB_CLIENT_SECRET and ADMIN_GITHUB_IDS, or use --demo.')
    directory = Path(__file__).parent / '.data'
    directory.mkdir(mode=0o700, exist_ok=True)
    server = make_server(directory / ('demo.sqlite3' if args.demo else 'wiki.sqlite3'),args.port,args.demo,
                         os.environ.get('ADMIN_GITHUB_IDS','').split(','))
    print('Open '+server.origin+' (local only)', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.store.db.close()
        server.server_close()


if __name__ == '__main__':
    main()
