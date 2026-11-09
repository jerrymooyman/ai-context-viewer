"""Sanitized acceptance journeys through the actual loopback API.

These tests would fail if snapshot reports became verification, context boundaries
carried claims forward, or viewing/importing/flagging changed source records.
"""
import copy
import hashlib
import http.client
import json
from pathlib import Path
import tempfile
import threading
import unittest

from viewer.logs import read_delta
from viewer.server import make_server


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.path = self.root / 'acceptance.jsonl'
        self.state = self.root / 'state.json'
        self.path.write_text('')
        self.append('session_meta', dict(id='acceptance', cli_version='0.167.0', cwd='/synthetic',
                                        base_instructions='Use the documented local procedure.'))
        self.append('response_item', dict(type='function_call', name='exec_command', call_id='procedure',
                                         arguments=json.dumps(dict(cmd='cat /synthetic/procedure.md'))))
        self.append('response_item', dict(type='function_call_output', call_id='procedure',
                                         output='Process exited with code 0\nOutput:\nUse the local tunnel procedure; keep the accepted sync tradeoff.'))
        key = str(read_delta(self.path, None).records[-1].key)
        self.snapshot = json.loads(Path('tests/fixtures/snapshots/goal.json').read_text())
        self.snapshot['sources'][0].update(title='/synthetic/procedure.md', evidence_keys=[key])
        self.snapshot['goal']['constraints'] = ['The sync delay is an accepted tradeoff.']
        self.snapshot['claims'] = [dict(id=identifier, scope=scope, statement=text, kind=kind,
             source_ids=['source1'], verification=dict(status='unknown', evidence_keys=[]))
             for identifier, scope, text, kind in [
                 ('explorer', 'a', 'The active explorer is assumed to be Tree A.', 'assumption'),
                 ('sync', 'goal', 'Retain the accepted sync tradeoff.', 'decision'),
                 ('procedure', 'b', 'Use the documented tunnel procedure.', 'constraint'),
                 ('status', 'goal', 'The handoff status may be stale.', 'assumption')]]
        self.append_snapshot(self.snapshot)
        self.start()
        self.addCleanup(self.stop)
        self.context = self.get('/api/sessions')['sessions'][0]['id']
        self.url = '/api/contexts/' + self.context

    def append(self, kind, payload):
        with self.path.open('a') as stream:
            stream.write(json.dumps(dict(type=kind, timestamp='2026-11-08T08:00:00Z', payload=payload)) + '\n')

    def append_snapshot(self, snapshot):
        self.append('response_item', dict(type='message', role='assistant', content=[
            dict(text='```context-viewer-snapshot\n' + json.dumps(snapshot) + '\n```')]))

    def start(self):
        self.server = make_server(self.root, 0, self.state)
        self.thread = threading.Thread(target=self.server.serve_forever)
        self.thread.start()
        self.port = self.server.server_port

    def stop(self):
        self.server.shutdown()
        self.thread.join(2)
        self.server.server_close()
        self.assertFalse(self.thread.is_alive())

    def request(self, route, payload=None, extra_headers=None):
        connection = http.client.HTTPConnection('127.0.0.1', self.port, timeout=3)
        try:
            connection.request('GET' if payload is None else 'POST', route,
                               body=None if payload is None else json.dumps(payload),
                               headers=({} if payload is None else {'Content-Type': 'application/json'}) | (extra_headers or {}))
            response = connection.getresponse()
            return response.status, json.loads(response.read())
        finally:
            connection.close()

    def get(self, route):
        status, body = self.request(route)
        self.assertEqual(status, 200, body)
        return body

    def post(self, route, payload):
        headers = {'X-Context-Generation': self.get(route.removesuffix('/imports')).get('generation', 'unavailable-log')} if route.endswith('/imports') else {}
        status, body = self.request(route, payload, headers)
        self.assertEqual(status, 200, body)
        return body

    def digest(self):
        return hashlib.sha256(self.path.read_bytes()).hexdigest()

    def test_acceptance_claims_sources_flags_import_and_restart_are_read_only(self):
        before = self.digest()
        view = self.get(self.url)
        self.assertEqual(len(view['snapshot']['claims']), 4)
        self.assertEqual(view['snapshot']['goal']['constraints'], ['The sync delay is an accepted tradeoff.'])
        source = next(s for s in view['sources'] if s['id'] == 'source1')
        self.assertEqual(set(source['scopes']), {'goal', 'a', 'b'})
        self.assertEqual(source['matches'][0]['status'], 'recorded-read')
        self.assertIn('local tunnel procedure', source['matches'][0]['text'])
        self.post(self.url + '/flags', dict(generation=view['generation'], source_id='source1',
                  evidence_key=None, reason='User concern: check stale handoff status.'))
        update = copy.deepcopy(self.snapshot)
        update.update(snapshot_id='s2', supersedes='s1')
        update['claims'][0]['statement'] = 'Explorer identity still needs a running-environment check.'
        self.post(self.url + '/imports', update)
        view = self.get(self.url)
        self.assertEqual(view['snapshot_history'][-1]['origin'], 'user-imported')
        self.assertTrue(all(c['verification']['status'] == 'unknown' for c in view['snapshot']['claims']))
        self.assertEqual(self.digest(), before)
        self.stop()
        self.start()
        restored = self.get(self.url)
        self.assertEqual(restored['snapshot']['snapshot_id'], 's1')  # imports are temporary
        self.assertEqual(restored['flags'][0]['author_type'], 'user')
        self.assertIn('stale handoff', restored['flags'][0]['reason'])
        self.assertEqual(self.digest(), before)

    def test_acknowledgment_compaction_interruption_and_unknown_event_do_not_verify(self):
        original = self.get(self.url)
        self.append('response_item', dict(type='message', role='assistant', content=[
            dict(text='I acknowledge the explorer correction; I have not checked it yet.')]))
        self.append('compacted', dict(summary='Synthetic compaction; retained contents unknown.'))
        self.append('event_msg', dict(type='turn_aborted', reason='interrupted'))
        self.append('future_record', dict(value='unknown evidence'))
        before = self.digest()
        view = self.get(self.url)
        self.assertEqual(view['snapshot'], original['snapshot'])
        self.assertEqual(view['snapshot_at'], original['snapshot_at'])
        self.assertTrue(any('acknowledge' in r['text'] for r in view['activity']))
        self.assertTrue(any('retained prompt' in r['warning'] for r in view['activity']))
        self.assertTrue(any('turn_aborted' in r['text'] for r in view['activity']))
        self.assertTrue(any(r['kind'] == 'unknown' for r in view['activity']))
        self.assertTrue(all(c['verification']['status'] == 'unknown' for c in view['snapshot']['claims']))
        self.assertEqual(self.digest(), before)

    def test_stale_or_missing_import_generation_is_rejected_before_retention(self):
        before = self.get(self.url)
        self.path.write_text(self.path.read_text().splitlines()[0] + '\n')
        update = copy.deepcopy(self.snapshot)
        status, body = self.request(self.url + '/imports', update,
                                   {'X-Context-Generation': before['generation']})
        self.assertEqual(status, 400)
        current = self.get(self.url)
        self.assertNotEqual(current['generation'], before['generation'])
        self.assertIsNone(current['snapshot'])
        self.assertEqual(current['snapshot_history'], [])
        self.assertEqual(self.request(self.url + '/imports', update)[0], 400)
        self.assertEqual(self.request(self.url + '/imports', update,
            {'X-Context-Generation': current['generation']})[0], 200)

    def test_declared_fresh_context_handoff_only_in_second_and_restart(self):
        first = self.get(self.url)
        self.append('response_item', dict(type='message', role='user', content=[dict(text='Fresh context begins here.')]))
        self.append('response_item', dict(type='message', role='developer', content=[dict(text='Second-context-only handoff.')]))
        view = self.get(self.url)
        key = next(r['key'] for r in view['supplied'] if r['text'] == 'Fresh context begins here.')
        second_id = self.post(self.url + '/boundaries', dict(record_key=key))['context_id']
        second_url = '/api/contexts/' + second_id
        second = self.get(second_url)
        self.assertIsNone(second['snapshot'])
        self.assertEqual(second['flags'], [])
        self.assertTrue(any('Second-context-only' in r['text'] for r in second['supplied']))
        first_after = self.get(self.url)
        self.assertEqual(first_after['snapshot'], first['snapshot'])
        self.assertFalse(any('Second-context-only' in r['text'] for r in first_after['supplied']))
        self.stop()
        self.start()
        self.assertIsNone(self.get(second_url)['snapshot'])
        self.assertEqual(len(self.get('/api/sessions')['sessions'][0]['contexts']), 2)


class FileWorkflowTests(unittest.TestCase):
    """Explicit file collection and evidence attachment through real HTTP."""
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.project = self.root / 'project'; self.project.mkdir()
        self.logs = self.root / 'logs'; self.logs.mkdir()
        self.state = self.root / 'state.json'
        self.path = self.log('a', 'session-a')
        self.log('b', 'session-b')
        self.log('outside', 'outside', project=self.project / 'child')
        self.start(); self.addCleanup(self.stop)

    def log(self, name, session, version='0.167.0', project=None):
        path = self.logs / (name + '.jsonl')
        path.write_text(json.dumps(dict(type='session_meta', payload=dict(id=session, cwd=str(project or self.project), cli_version=version))) + '\n' +
            json.dumps(dict(type='response_item', timestamp='2026-11-08T00:00:00Z', payload=dict(type='message', role='user', content=[dict(text='Synthetic source instruction.')]))) + '\n')
        return path

    def start(self):
        self.server = make_server(self.logs, 0, self.state, project=self.project)
        self.thread = threading.Thread(target=self.server.serve_forever); self.thread.start()
        self.port = self.server.server_port

    def stop(self):
        self.server.shutdown(); self.thread.join(2); self.server.server_close()

    def request(self, route, payload=None):
        connection = http.client.HTTPConnection('127.0.0.1', self.port, timeout=3)
        try:
            connection.request('GET' if payload is None else 'POST', route,
                body=None if payload is None else json.dumps(payload),
                headers={} if payload is None else {'Content-Type':'application/json'})
            response = connection.getresponse(); return response.status, json.loads(response.read())
        finally: connection.close()

    def get(self, route):
        status, body = self.request(route); self.assertEqual(status, 200, body); return body

    def collect(self):
        status, body = self.request('/api/collect', {}); self.assertEqual(status, 200, body); return body

    def export(self, session='session-a', task='docs/progress.md', parent=None, binding=None, snapshot=None):
        from viewer.export_snapshot import export_snapshot
        payload = copy.deepcopy(snapshot or json.loads(Path('tests/fixtures/snapshots/goal.json').read_text()))
        payload.pop('snapshot_id'); payload['supersedes'] = parent
        key = str(read_delta(self.path, None).records[-1].key)
        payload['sources'][0]['evidence_keys'] = [key]
        path = export_snapshot(self.project, session, payload, expected_head=parent, task_ref=task,
            context_binding=binding, log_root=self.logs, state_file=self.state)
        return json.loads(path.read_text())['snapshot']['snapshot_id']

    def test_file_history_opens_referenced_local_file(self):
        from unittest.mock import patch
        from types import SimpleNamespace
        payload = json.loads(Path('tests/fixtures/snapshots/goal.json').read_text())
        payload['sources'][0]['title'] = 'guide.md — current guide'
        target = self.project / 'guide.md'
        target.write_text('# Local guide')
        self.export(snapshot=payload)
        history = self.collect()['file_histories'][0]
        route = '/api/file-histories/' + history['history_id'] + '/open-local'
        with patch('viewer.source_files.subprocess.run', return_value=SimpleNamespace(returncode=0)) as launch:
            status, body = self.request(route, {'source_id':payload['sources'][0]['id']})
            self.assertEqual(status, 200, body)
            self.assertEqual(launch.call_args.args[0], ['xdg-open', str(target)])
            launch.reset_mock()
            self.assertEqual(self.request(route, {'source_id':'missing'})[0], 400)
            launch.assert_not_called()

    def test_export_waits_for_collection_restart_and_exact_project_scope(self):
        before = hashlib.sha256(self.path.read_bytes()).hexdigest()
        self.assertEqual(len(self.get('/api/sessions')['sessions']), 2)
        head = self.export()
        self.assertEqual(self.get('/api/sessions')['file_histories'], [])
        data = self.collect(); history = data['file_histories'][0]
        self.assertEqual(history['session_id'], 'session-a')
        url = '/api/file-histories/' + history['history_id']
        view = self.get(url)
        self.assertEqual(view['snapshot']['snapshot_id'], head)
        self.assertEqual(view['attachment'], 'attached')
        self.assertEqual(view['sources'][0]['matches'][0]['status'], 'recorded-supplied')
        age = view['snapshot_at']
        next_head = self.export(parent=head)
        self.assertEqual(self.get(url)['snapshot']['snapshot_id'], head)
        self.assertEqual(self.get(url)['snapshot_at'], age)
        self.collect(); self.assertEqual(self.get(url)['snapshot']['snapshot_id'], next_head)
        self.stop(); self.start()
        self.assertEqual(self.get(url)['snapshot']['snapshot_id'], next_head)
        self.assertEqual(hashlib.sha256(self.path.read_bytes()).hexdigest(), before)

    def test_task_session_null_and_unresolved_histories_do_not_borrow_evidence(self):
        self.export(); self.export('session-b'); self.export(task=None); self.export(task='docs/other.md')
        self.export('missing'); self.log('unsupported','unsupported',version='999'); self.export('unsupported')
        data = self.collect(); self.assertEqual(len(data['file_histories']),6)
        self.assertEqual(len({h['history_id'] for h in data['file_histories']}),6)
        self.assertTrue(any(h['task_ref'] is None for h in data['file_histories']))
        for item in data['file_histories']:
            view = self.get('/api/file-histories/' + item['history_id'])
            if item['session_id'] in ('missing','unsupported'):
                self.assertNotEqual(view['attachment'],'attached')
                self.assertTrue(all(not s['matches'] or all(m['status']=='unresolved' for m in s['matches']) for s in view['sources']))

    def test_manual_target_detaches_after_boundary_removal_and_generation_change(self):
        summary = next(s for s in self.get('/api/sessions')['sessions'] if s['session_id']=='session-a')
        url = '/api/contexts/' + summary['id']; first = self.get(url)
        status, created = self.request(url + '/boundaries', {'record_key':first['supplied'][0]['key']})
        self.assertEqual(status,200,created)
        manual = self.get('/api/contexts/' + created['context_id'])
        binding = manual['context_target']['context_binding']
        self.export(binding=binding); history = self.collect()['file_histories'][0]
        route = '/api/file-histories/' + history['history_id']
        self.assertEqual(self.get(route)['attachment'],'attached')
        self.assertEqual(self.request('/api/contexts/' + created['context_id'] + '/boundaries',{'action':'remove'})[0],200)
        self.assertEqual(self.get(route)['attachment'],'unresolved')
        self.assertEqual(self.get(route)['supplied'],[])
        # An initial binding also detaches when the original file generation changes.
        self.export(task='initial.md'); self.collect()
        initial = next(h for h in self.get('/api/sessions')['file_histories'] if h['task_ref'].endswith('initial.md'))
        initial_route = '/api/file-histories/' + initial['history_id']
        self.assertEqual(self.get(initial_route)['attachment'],'attached')
        self.path.write_text(self.path.read_text().splitlines()[0] + '\n')
        # Initial bindings have no generation identity: current initial evidence is allowed,
        # but old evidence keys must resolve as unavailable rather than borrow new bytes.
        changed = self.get(initial_route)
        self.assertTrue(all(m['status']=='unresolved' for s in changed['sources'] for m in s['matches']))

    def test_ambiguous_identity_and_cross_stream_conflict_keep_chains_separate(self):
        self.export()
        history = self.collect()['file_histories'][0]; route='/api/file-histories/'+history['history_id']
        self.log('duplicate','session-a')
        view = self.get(route); self.assertEqual(view['attachment'],'ambiguous'); self.assertEqual(view['supplied'],[])
        (self.logs/'duplicate.jsonl').unlink()
        snapshot = copy.deepcopy(view['snapshot']); snapshot['goal']['objective']='Different log report'
        with self.path.open('a') as stream:
            stream.write(json.dumps(dict(type='response_item',timestamp='2026-11-08T01:00:00Z',payload=dict(type='message',role='assistant',content=[dict(text='```context-viewer-snapshot\n'+json.dumps(snapshot)+'\n```')])))+'\n')
        current = self.get(route)
        self.assertEqual(current['snapshot']['goal']['objective'], view['snapshot']['goal']['objective'])
        self.assertEqual(current['cross_stream'][0]['status'],'conflict')

    def test_paged_history_preserves_current_head_and_rejects_changed_cursor(self):
        head = None
        for i in range(129): head=self.export(parent=head)
        history=self.collect()['file_histories'][0]; route='/api/file-histories/'+history['history_id']
        first=self.get(route); self.assertEqual(len(first['snapshot_history']),100)
        second=self.get(route+'?cursor='+first['history_next'])
        self.assertEqual(len(second['snapshot_history']),29)
        self.assertEqual(second['current_report']['snapshot']['snapshot_id'],head)
        self.assertEqual(second['snapshot_at'],first['snapshot_at'])
        self.export(parent=head); self.collect()
        self.assertEqual(self.request(route+'?cursor='+first['history_next'])[0],400)
        self.assertEqual(self.request(route+'?limit=101')[0],400)

    def test_file_flags_are_isolated_and_saved_state_retains_them(self):
        self.export(); self.export(task='docs/other.md')
        histories=self.collect()['file_histories']; route='/api/file-histories/'+histories[0]['history_id']
        view=self.get(route)
        status,body=self.request(route+'/flags',dict(generation=view['generation'],source_id='source1',evidence_key=None,reason='Check this file source.'))
        self.assertEqual(status,200,body)
        self.assertEqual(self.get(route)['flags'][0]['reason'],'Check this file source.')
        self.assertEqual(self.get('/api/file-histories/'+histories[1]['history_id'])['flags'],[])
        recorded=next(s for s in self.get('/api/sessions')['sessions'] if s['session_id']=='session-a')
        self.assertEqual(self.get('/api/contexts/'+recorded['id'])['flags'],[])
        self.stop();self.start()
        self.assertEqual(len(self.get(route)['flags']),1)

    def test_file_history_corruption_retains_last_selected_understanding_with_warning(self):
        self.export(); history=self.collect()['file_histories'][0]
        route='/api/file-histories/'+history['history_id']; before=self.get(route)
        self.state.with_suffix('.snapshots.sqlite3').write_bytes(b'corrupt')
        data=self.collect(); self.assertFalse(data['collection']['complete'])
        self.assertEqual(len(data['file_histories']),1)
        retained=self.get(route)
        self.assertEqual(retained['snapshot'],before['snapshot'])
        self.assertTrue(any('corrupt' in w for w in retained['warnings']))
        self.assertEqual(retained['attachment'],'unresolved')

    def test_collection_routes_preserve_origin_body_and_path_protection(self):
        self.export(); history=self.collect()['file_histories'][0]
        route='/api/file-histories/'+history['history_id']
        connection=http.client.HTTPConnection('127.0.0.1',self.port,timeout=3)
        try:
            connection.request('POST','/api/collect',body='{}',headers={'Content-Type':'application/json','Origin':'https://example.invalid'})
            response=connection.getresponse();self.assertEqual(response.status,403);response.read()
        finally:connection.close()
        self.assertEqual(self.request('/api/collect',{'project':'/etc'})[0],400)
        self.assertEqual(self.request(route+'?filename=/etc/passwd')[0],400)
        self.assertEqual(self.request('/api/file-histories/../../etc/passwd')[0],404)

    def test_cross_stream_equal_payload_links_provenance_without_combining_chains(self):
        self.export();history=self.collect()['file_histories'][0];route='/api/file-histories/'+history['history_id']
        original=self.get(route)['snapshot']
        with self.path.open('a') as stream:
            stream.write(json.dumps(dict(type='response_item',timestamp='2026-11-08T01:00:00Z',payload=dict(type='message',role='assistant',content=[dict(text='```context-viewer-snapshot\n'+json.dumps(original)+'\n```')])))+'\n')
        view=self.get(route)
        self.assertEqual(view['cross_stream'][0]['status'],'linked')
        self.assertEqual(view['snapshot'],original)
        self.assertEqual(view['history_count'],1)

    def test_identity_duplicate_outside_project_still_disables_evidence(self):
        self.export();history=self.collect()['file_histories'][0]
        self.log('elsewhere','session-a',project=self.root/'other')
        view=self.get('/api/file-histories/'+history['history_id'])
        self.assertEqual(view['attachment'],'ambiguous')
        self.assertEqual(view['supplied'],[])

    def test_cross_stream_old_conflict_is_visible_on_first_history_page(self):
        self.export()
        first_history=self.collect()['file_histories'][0]
        route='/api/file-histories/'+first_history['history_id']
        original=self.get(route)['snapshot']
        head=original['snapshot_id']
        for i in range(128): head=self.export(parent=head)
        self.collect()
        original['goal']['objective']='Conflicting old report in log'
        with self.path.open('a') as stream:
            stream.write(json.dumps(dict(type='response_item',timestamp='2026-11-08T01:00:00Z',payload=dict(type='message',role='assistant',content=[dict(text='```context-viewer-snapshot\n'+json.dumps(original)+'\n```')])))+'\n')
        view=self.get(route)
        self.assertNotIn(original['snapshot_id'],[e['snapshot']['snapshot_id'] for e in view['snapshot_history']])
        self.assertEqual(view['cross_stream'][0]['report_id'],original['snapshot_id'])
        self.assertEqual(view['cross_stream'][0]['status'],'conflict')
