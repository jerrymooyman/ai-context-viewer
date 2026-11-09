import hashlib
import http.client
import json
from pathlib import Path
import tempfile
import threading
import unittest

from viewer.server import make_server


class ServerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.path = self.root / 'sample.jsonl'
        self.path.write_text('\n'.join(json.dumps(row) for row in [
            {'type': 'session_meta', 'payload': {'id': 'a', 'cli_version': '0.167.0', 'source': 'vscode', 'cwd': '/sample'}},
            {'type': 'response_item', 'timestamp': '2026-11-08T01:00:00Z', 'payload': {'type': 'message', 'role': 'user', 'content': [{'text': '<img src=x onerror=alert(1)> café'}]}},
            {'type': 'event_msg', 'timestamp': '2026-11-08T01:01:00Z', 'payload': {'type': 'task_started'}}
        ]) + '\n')
        self.before = hashlib.sha256(self.path.read_bytes()).hexdigest()
        self.server = make_server(self.root, 0, self.root / 'state.json')
        self.addCleanup(self.server.server_close)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop)
        self.port = self.server.server_address[1]

    def stop(self):
        self.server.shutdown()
        self.thread.join(2)

    def request(self, path, method='GET', headers=None, body=None):
        connection = http.client.HTTPConnection('127.0.0.1', self.port, timeout=3)
        try:
            connection.request(method, path, body=body, headers=headers or {})
            response = connection.getresponse()
            return response.status, dict(response.getheaders()), response.read()
        finally:
            connection.close()

    def test_explicit_selection_and_read_only_context(self):
        status, _, body = self.request('/api/sessions')
        self.assertEqual(status, 200)
        sessions = json.loads(body)['sessions']
        self.assertEqual(len(sessions), 1)
        self.assertNotIn('path', sessions[0])
        status, _, body = self.request('/api/contexts/' + sessions[0]['id'])
        view = json.loads(body)
        self.assertEqual(status, 200)
        self.assertEqual(view['supplied'][0]['text'], '<img src=x onerror=alert(1)> café')
        self.assertEqual(view['activity_at'], '2026-11-08T01:01:00Z')
        self.assertIsNone(view['snapshot_at'])
        self.assertTrue(view['warnings'])
        self.assertEqual(hashlib.sha256(self.path.read_bytes()).hexdigest(), self.before)

    def test_host_origin_and_fetch_site_rejections(self):
        for headers in [{'Host': 'attacker.example'}, {'Origin': 'https://attacker.example'},
                        {'Origin': 'null'}, {'Sec-Fetch-Site': 'cross-site'},
                        {'Host': f'127.0.0.1:{self.port}', 'Origin': f'http://localhost:{self.port}'}]:
            with self.subTest(headers=headers):
                self.assertEqual(self.request('/api/sessions', headers=headers)[0], 403)
        self.assertEqual(self.request('/api/sessions', headers={'Origin': f'http://127.0.0.1:{self.port}'})[0], 200)

    def test_traversal_unknown_ids_and_mutations_rejected(self):
        for path in ['/../AGENTS.md', '/%2e%2e/AGENTS.md', '/api/contexts/../../etc/passwd', '/api/contexts/unknown', '/web/../AGENTS.md']:
            self.assertEqual(self.request(path)[0], 404)
        self.assertEqual(self.request('/api/sessions', 'POST', body='{}')[0], 405)
        self.assertEqual(self.request('/api/sessions', headers={'Content-Length': '9999999'})[0], 413)

    def test_static_security_headers_and_unknown_version(self):
        status, headers, body = self.request('/')
        self.assertEqual(status, 200)
        self.assertIn(b'Choose a context', body)
        self.assertIn("script-src 'self'", headers['Content-Security-Policy'])
        # Firefox sends Origin: null for same-origin POSTs under no-referrer.
        self.assertEqual(headers['Referrer-Policy'], 'same-origin')
        self.assertEqual(headers['X-Content-Type-Options'], 'nosniff')
        self.assertEqual(headers['Cache-Control'], 'no-store')
        self.assertNotIn('Access-Control-Allow-Origin', headers)
        self.path.write_text(self.path.read_text().replace('0.167.0', '99.0'))
        sessions = json.loads(self.request('/api/sessions')[2])['sessions']
        view = json.loads(self.request('/api/contexts/' + sessions[0]['id'])[2])
        self.assertEqual(view['status'], 'unsupported')
        self.assertEqual(view['supplied'], [])

    def test_removed_session_and_missing_root(self):
        session = json.loads(self.request('/api/sessions')[2])['sessions'][0]
        self.path.unlink()
        self.assertEqual(self.request('/api/contexts/' + session['id'])[0], 404)
        self.assertEqual(json.loads(self.request('/api/sessions')[2])['sessions'], [])
        self.root.rmdir()
        data = json.loads(self.request('/api/sessions')[2])
        self.assertEqual(data['status'], 'unavailable')

    def test_source_link_serves_current_markdown_as_plain_text(self):
        from test_snapshots import sample
        rows = self.path.read_text().splitlines()
        metadata = json.loads(rows[0])
        metadata['payload']['cwd'] = str(self.root)
        rows[0] = json.dumps(metadata)
        self.path.write_text('\n'.join(rows) + '\n')
        (self.root / 'guide.md').write_text('# Actual markdown\n<script>literal</script>')
        session = json.loads(self.request('/api/sessions')[2])['sessions'][0]
        url = '/api/contexts/' + session['id']
        snapshot = sample()
        snapshot['sources'] = [{'id': 'source-file', 'title': 'guide.md', 'evidence_keys': []}]
        snapshot['claims'] = []
        generation = json.loads(self.request(url)[2])['generation']
        headers = {'Content-Type': 'application/json', 'X-Context-Generation': generation}
        self.assertEqual(self.request(url + '/imports', 'POST', headers, json.dumps(snapshot))[0], 200)
        status, headers, body = self.request(url + '?source=source-file')
        self.assertEqual(status, 200)
        self.assertIn('text/plain', headers['Content-Type'])
        self.assertIn(b'# Actual markdown\n<script>literal</script>', body)
        self.assertIn(b'not historical captured evidence', body)
        self.assertEqual(self.request(url + '?source=missing')[0], 400)

    def test_all_browser_assets_are_served(self):
        self.assertEqual(self.server.server_address[0], '127.0.0.1')
        for route in ['/app.js', '/model.mjs', '/styles.css']:
            with self.subTest(route=route):
                status, headers, body = self.request(route)
                self.assertEqual(status, 200)
                self.assertGreater(len(body), 30)

    def test_retargeted_symlink_cannot_expose_an_external_file(self):
        session = json.loads(self.request('/api/sessions')[2])['sessions'][0]
        outside = self.root.parent / (self.root.name + '-private.jsonl')
        outside.write_text(self.path.read_text())
        self.addCleanup(outside.unlink)
        self.path.unlink()
        self.path.symlink_to(outside)
        self.assertEqual(self.request('/api/contexts/' + session['id'])[0], 404)

    def test_boundary_api_paging_and_restart_state(self):
        session = json.loads(self.request('/api/sessions')[2])['sessions'][0]
        url = '/api/contexts/' + session['id']
        view = json.loads(self.request(url)[2])
        status, _, body = self.request(url + '/boundaries', 'POST',
            {'Content-Type': 'application/json'}, json.dumps({'record_key': view['activity'][0]['key']}))
        self.assertEqual(status, 200)
        second_id = json.loads(body)['context_id']
        second = json.loads(self.request('/api/contexts/' + second_id)[2])
        self.assertEqual(second['supplied'], [])
        self.assertEqual(len(second['activity']), 1)
        self.assertEqual(len(json.loads(self.request('/api/sessions')[2])['sessions'][0]['contexts']), 2)
        self.assertEqual(self.request(url + '?limit=999')[0], 400)
        self.assertEqual(self.request(url + '/boundaries', 'POST',
            {'Content-Type': 'application/json', 'Origin': 'https://foreign.example'}, '{}')[0], 403)
        self.assertEqual(hashlib.sha256(self.path.read_bytes()).hexdigest(), self.before)
        self.assertEqual(self.request('/api/contexts/' + second_id + '/boundaries', 'POST',
            {'Content-Type': 'application/json'}, '{"action":"remove"}')[0], 200)

    def test_snapshot_imports_and_independent_context_without_logs(self):
        from test_snapshots import sample
        session = json.loads(self.request('/api/sessions')[2])['sessions'][0]
        url = '/api/contexts/' + session['id']
        headers = {'Content-Type': 'application/json', 'X-Context-Generation': json.loads(self.request(url)[2])['generation']}
        self.assertEqual(self.request(url + '/imports', 'POST', headers, json.dumps(sample()))[0], 200)
        view = json.loads(self.request(url)[2])
        self.assertEqual(view['snapshot_history'][0]['origin'], 'user-imported')
        self.assertEqual(self.request(url + '/imports', 'POST', headers, '{"x":1,"x":2}')[0], 400)
        self.assertEqual(len(json.loads(self.request(url)[2])['snapshot_history']), 1)
        self.path.unlink()
        status, _, body = self.request('/api/import-contexts', 'POST', headers, '{}')
        self.assertEqual(status, 200)
        context = json.loads(body)['context_id']
        headers['X-Context-Generation'] = 'unavailable-log'
        self.assertEqual(self.request('/api/contexts/' + context + '/imports', 'POST', headers, json.dumps(sample()))[0], 200)
        view = json.loads(self.request('/api/contexts/' + context)[2])
        self.assertEqual(view['status'], 'user-imported')
        self.assertIsNone(view['activity_at'])
        self.assertEqual(len(json.loads(self.request('/api/sessions')[2])['sessions']), 1)

    def test_flag_api_attribution_validation_and_read_only_log(self):
        session=json.loads(self.request('/api/sessions')[2])['sessions'][0]
        url='/api/contexts/'+session['id']
        view=json.loads(self.request(url)[2])
        key=view['sources'][0]['evidence_keys'][0]
        body=json.dumps(dict(generation=view['generation'],source_id=None,evidence_key=key,reason='Unrelated <img>'))
        headers={'Content-Type':'application/json'}
        status,_,raw=self.request(url+'/flags','POST',headers,body)
        self.assertEqual(status,200)
        self.assertEqual(json.loads(raw)['flag']['author_type'],'user')
        flags=json.loads(self.request(url)[2])['flags']
        self.assertEqual(flags[0]['reason'],'Unrelated <img>')
        self.assertEqual(self.request(url+'/flags','POST',dict(headers,Origin='https://foreign.example'),body)[0],403)
        self.assertEqual(self.request(url+'/flags','POST',headers,body.replace(view['generation'],'stale'))[0],400)
        self.assertEqual(hashlib.sha256(self.path.read_bytes()).hexdigest(),self.before)
