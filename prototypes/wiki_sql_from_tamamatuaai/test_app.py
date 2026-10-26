import tempfile
import unittest
from pathlib import Path
from app import Store


class ContributionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name)/'test.sqlite3'
        self.s = Store(self.path)
        self.s.user('1','editor')
        self.s.user('2','admin')

    def tearDown(self):
        self.s.db.close()
        self.tmp.cleanup()

    def field(self):
        return self.s.db.execute('SELECT description FROM records WHERE id=1').fetchone()[0]

    def test_pending_blank_field_does_not_publish(self):
        cid = self.s.propose('1',1,'description','','New information')
        self.assertEqual(self.field(),'')
        c=self.s.db.execute('SELECT * FROM changes WHERE id=?',(cid,)).fetchone()
        self.assertEqual((c['author'],c['status']),('1','pending'))
        self.assertEqual(self.s.review(cid,'2',True,True),'approved')
        self.assertEqual(self.field(),'New information')

    def test_existing_field_and_rejection(self):
        cid=self.s.propose('1',1,'name','Community resource','Replacement')
        self.assertEqual(self.s.review(cid,'2',False,True),'rejected')
        self.assertEqual(self.s.db.execute('SELECT name FROM records WHERE id=1').fetchone()[0],'Community resource')

    def test_gate_off_immediate_and_history_persistent(self):
        self.s.set_gate(False,True)
        self.s.propose('1',1,'description','','Immediate')
        self.s.db.close()
        self.s=Store(self.path)
        self.assertEqual(self.field(),'Immediate')
        self.assertFalse(self.s.gate())
        self.assertEqual(self.s.db.execute('SELECT author FROM changes').fetchone()[0],'1')

    def test_non_admin_cannot_review_or_toggle(self):
        cid=self.s.propose('1',1,'description','','Proposed')
        with self.assertRaises(PermissionError): self.s.review(cid,'1',True,False)
        with self.assertRaises(PermissionError): self.s.set_gate(False,False)
        self.assertEqual(self.field(),'')

    def test_conflicting_approval_preserves_first_edit(self):
        a=self.s.propose('1',1,'description','','First')
        b=self.s.propose('1',1,'description','','Second')
        self.s.review(a,'2',True,True)
        self.assertEqual(self.s.review(b,'2',True,True),'conflict')
        self.assertEqual(self.field(),'First')

    def test_stale_form_and_duplicate_decision(self):
        self.s.set_gate(False,True)
        cid=self.s.propose('1',1,'description','','Updated')
        with self.assertRaises(ValueError): self.s.propose('1',1,'description','','Stale')
        with self.assertRaises(ValueError): self.s.review(cid,'2',True,True)

    def test_gate_toggle_does_not_publish_pending(self):
        self.s.propose('1',1,'description','','Pending')
        self.s.set_gate(False,True)
        self.assertEqual(self.field(),'')
        self.s.set_gate(True,True)
        self.assertEqual(self.field(),'')

    def test_unauthenticated_and_invalid_input(self):
        with self.assertRaises(PermissionError): self.s.propose('missing',1,'description','','x')
        with self.assertRaises(ValueError): self.s.propose('1',1,'bad_column','','x')
        with self.assertRaises(ValueError): self.s.propose('1',999,'name','','x')
        with self.assertRaises(ValueError): self.s.propose('1',1,'description','','x'*2001)




import http.client
import re
import threading
from unittest.mock import patch
from urllib.parse import urlencode, urlsplit, parse_qs
from app import make_server


class HttpTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.server = make_server(Path(self.tmp.name)/'http.sqlite3',0,True)
        self.thread = threading.Thread(target=self.server.serve_forever,daemon=True)
        self.thread.start()
        self.cookie = ''

    def tearDown(self):
        self.server.shutdown()
        self.thread.join()
        self.server.server_close()
        self.server.store.db.close()
        self.tmp.cleanup()

    def request(self, path='/', data=None, headers=None):
        c = http.client.HTTPConnection('127.0.0.1',self.server.server_port,timeout=3)
        h = {'Cookie':self.cookie}
        if headers: h.update(headers)
        body = urlencode(data) if data is not None else None
        c.request('POST' if data is not None else 'GET',path,body,h)
        r = c.getresponse()
        status, response_headers, text = r.status, dict(r.getheaders()), r.read().decode()
        if 'Set-Cookie' in response_headers:
            self.cookie = response_headers['Set-Cookie'].split(';')[0]
        c.close()
        return status,response_headers,text

    def csrf(self):
        return re.search('name="csrf" value="([^"]+)"',self.request()[2]).group(1)

    def login(self, name='demo-editor'):
        return self.request('/demo',{'csrf':self.csrf(),'user':name})

    def test_csrf_host_and_authorization(self):
        self.assertEqual(self.request(headers={'Host':'evil.example'})[0],400)
        self.assertEqual(self.request('/edit',{'csrf':'invalid'})[0],403)
        self.assertEqual(self.request('/gate',{'csrf':self.csrf(),'enabled':'0'})[0],403)
        self.login()
        self.assertEqual(self.request('/gate',{'csrf':self.csrf(),'enabled':'0'})[0],403)
        self.assertEqual(self.request('/review',{'csrf':self.csrf(),'change':'1','decision':'approve'})[0],403)

    def test_complete_browser_flow_and_escaping(self):
        self.assertEqual(self.login()[0],303)
        token=self.csrf()
        self.assertEqual(self.request('/edit',{'csrf':token,'record':'1','field':'description','old':'','value':'<script>alert(1)</script>'})[0],303)
        page=self.request()[2]
        self.assertIn('&lt;script&gt;',page)
        self.assertNotIn('<script>',page)
        self.assertIn('pending',page)
        self.request('/logout',{'csrf':token})
        self.login('demo-admin')
        self.assertEqual(self.request('/review',{'csrf':self.csrf(),'change':'1','decision':'approve'})[0],303)
        self.assertIn('approved',self.request()[2])
        self.assertEqual(self.server.store.db.execute('SELECT description FROM records WHERE id=1').fetchone()[0],'<script>alert(1)</script>')

    def test_oauth_state_rotation_and_stable_admin_identity(self):
        self.server.demo=False
        self.server.admins={'42'}
        with patch.dict('os.environ',{'GITHUB_CLIENT_ID':'test','GITHUB_CLIENT_SECRET':'test'}):
            status,headers,_=self.request('/login')
            self.assertEqual(status,303)
            query=parse_qs(urlsplit(headers['Location']).query)
            self.assertNotIn('scope',query)
            self.assertEqual(query['code_challenge_method'],['S256'])
            old_cookie=self.cookie
            with patch('app.github_json', side_effect=[{'access_token':'temporary'}, {'id':42,'login':'test-admin'}]) as provider:
                result=self.request('/callback?'+urlencode({'state':query['state'][0],'code':'test-code'}))
                self.assertEqual(result[0],303)
                self.assertEqual(provider.call_count,2)
            self.assertNotEqual(self.cookie,old_cookie)
            self.assertIn('Administrator controls',self.request()[2])
            self.assertEqual(self.request('/callback?'+urlencode({'state':query['state'][0],'code':'test-code'}))[0],400)
            self.assertEqual(self.request('/demo',{'csrf':self.csrf(),'user':'demo-admin'})[0],404)

    def test_oauth_invalid_state_never_calls_provider(self):
        self.server.demo=False
        with patch.dict('os.environ',{'GITHUB_CLIENT_ID':'test'}):
            self.request('/login')
            with patch('app.github_json') as provider:
                self.assertEqual(self.request('/callback?state=wrong&code=bad')[0],400)
                provider.assert_not_called()

if __name__ == '__main__':
    unittest.main()
