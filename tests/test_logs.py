import json
import os
from pathlib import Path
import tempfile
import unittest

from viewer.logs import discover_sessions, read_delta, RecordKey
from viewer.codex_0167 import normalize


class LogTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def write(self, name, rows):
        path = self.root / name
        path.write_text(''.join(json.dumps(row, ensure_ascii=False) + '\n' for row in rows))
        return path

    def test_discovery_keeps_misleading_source_and_unknown_versions(self):
        for name, version in [('a', '0.167.0'), ('b', '99.0')]:
            self.write(name + '.jsonl', [dict(type='session_meta', payload=dict(
                id=name, cli_version=version, source='vscode', cwd='/sample/project'))])
        sessions = discover_sessions(self.root)
        self.assertEqual(len(sessions), 2)
        self.assertEqual({s.recorded_source for s in sessions}, {'vscode'})
        self.assertEqual({s.status for s in sessions}, {'supported', 'unsupported'})
        self.assertNotIn('/', sessions[0].id)

    def test_sessions_order_by_modified_time_not_filename(self):
        for name, modified in [('a-old', 100), ('z-new', 300), ('m-middle', 200)]:
            path = self.write(name + '.jsonl', [dict(type='session_meta', payload=dict(
                id=name, cli_version='0.167.0'))])
            os.utime(path, (modified, modified))
        self.assertEqual([s.session_id for s in discover_sessions(self.root)],
                         ['z-new', 'm-middle', 'a-old'])
        os.utime(self.root / 'a-old.jsonl', (400, 400))
        self.assertEqual(discover_sessions(self.root)[0].session_id, 'a-old')

    def test_equal_modified_times_keep_stable_filename_order(self):
        for name in ['z', 'a']:
            path = self.write(name + '.jsonl', [])
            os.utime(path, (100, 100))
        self.assertEqual([s.path.name for s in discover_sessions(self.root)],
                         ['a.jsonl', 'z.jsonl'])

    def test_absent_root_and_invalid_metadata_are_visible(self):
        self.assertEqual(discover_sessions(self.root / 'missing'), [])
        self.write('bad.jsonl', [{'type': 'session_meta', 'payload': []}])
        self.assertEqual(discover_sessions(self.root)[0].status, 'unreadable')

    def test_discovery_excludes_symlink_escape(self):
        outside = self.root.parent / (self.root.name + '-outside.jsonl')
        outside.write_text('{}\n')
        self.addCleanup(outside.unlink)
        (self.root / 'escape.jsonl').symlink_to(outside)
        self.assertEqual(discover_sessions(self.root), [])

    def test_read_complete_lines_with_unicode_and_partial_tail(self):
        path = self.write('log.jsonl', [{'type': 'event_msg', 'payload': {'message': 'café 🦜'}}])
        with path.open('ab') as stream:
            stream.write(b'{"incomplete":')
        batch = read_delta(path, None)
        self.assertEqual(len(batch.records), 1)
        self.assertEqual(batch.records[0].record['payload']['message'], 'café 🦜')
        self.assertEqual(batch.warnings, [])
        self.assertEqual(read_delta(path, batch.cursor).records, [])

    def test_unknown_record_and_malformed_lines_do_not_hide_activity(self):
        path = self.root / 'log.jsonl'
        path.write_text('bad\n{"type":"future","payload":{}}\n')
        batch = read_delta(path, None)
        self.assertEqual(len(batch.warnings), 1)
        evidence = normalize(batch.records[0].record, batch.records[0].key)
        self.assertEqual(evidence[0].kind, 'unknown')
        self.assertIn('Unsupported', evidence[0].warning)

    def test_supplied_content_is_distinct_from_calls_outputs_and_reports(self):
        key = RecordKey('file', 0, 10)
        def item(payload):
            return normalize({'type': 'response_item', 'timestamp': 'now', 'payload': payload}, key)[0]
        supplied = item(dict(type='message', role='developer', content=[dict(type='input_text', text='<script>🦜</script>')]))
        report = item(dict(type='message', role='assistant', content=[dict(type='output_text', text='I checked it')]))
        call = item(dict(type='custom_tool_call', call_id='c', name='exec', input='cat file'))
        output = item(dict(type='custom_tool_call_output', call_id='c', output='captured'))
        self.assertEqual(supplied.kind, 'supplied')
        self.assertEqual(supplied.text, '<script>🦜</script>')
        self.assertEqual(report.kind, 'agent_message')
        self.assertEqual(call.kind, 'tool_call')
        self.assertEqual(output.kind, 'tool_output')
        self.assertEqual(output.call_id, 'c')
        self.assertEqual(output.coverage, 'unknown')

    def test_metadata_base_instructions_are_captured(self):
        evidence = normalize(dict(type='session_meta', payload=dict(base_instructions={'text': 'Recorded rules'})), RecordKey('f', 0, 0))
        self.assertEqual(evidence[0].text, 'Recorded rules')
        self.assertEqual(evidence[0].kind, 'supplied')

    def test_nonfinite_json_metadata_and_records_are_rejected(self):
        path = self.root / 'nonfinite.jsonl'
        path.write_text('{"type":"session_meta","payload":{"source":NaN}}\n')
        self.assertEqual(discover_sessions(self.root)[0].status, 'unreadable')
        batch = read_delta(path, None)
        self.assertEqual(batch.records, [])
        self.assertEqual(len(batch.warnings), 1)

    def test_schema_drift_keeps_unknown_content_visible(self):
        record = {'type': 'response_item', 'payload': {'type': 'message', 'role': 'user', 'content': [{'type': 'image', 'url': 'unresolved'}]}}
        evidence = normalize(record, RecordKey('f', 0, 1))[0]
        self.assertIn('Unsupported content part', evidence.text)
        self.assertEqual(evidence.coverage, 'unknown')

    def test_oversized_record_reports_omitted_evidence(self):
        path = self.root / 'large.jsonl'
        path.write_bytes(b'"' + b'x' * (1024 * 1024) + b'"\n')
        batch = read_delta(path, None)
        self.assertTrue(batch.limited)
        self.assertEqual(batch.records, [])
        self.assertIn('exceeds', batch.warnings[0])
