"""Read-only synthetic identity routing and immutable publication journeys."""
from copy import deepcopy
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from test_snapshots import sample
from viewer import export_snapshot as ex
from viewer import snapshot_files as sf
from viewer.logs import discover_sessions, read_delta


class ExportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve(); self.project = self.root / 'project'; self.project.mkdir()
        self.logs = self.root / 'logs'; self.logs.mkdir(); self.state = self.root / 'state.json'

    def report(self, head=None):
        value = sample(supersedes=head); value.pop('snapshot_id'); return value

    def export(self, payload=None, expected_head=None, **kwargs):
        return ex.export_snapshot(self.project, 'session-a', payload or self.report(), expected_head=expected_head,
                                  log_root=self.logs, state_file=self.state, **kwargs)

    def inspect(self, **kwargs):
        return ex.inspect_target(self.project, 'session-a', log_root=self.logs, state_file=self.state, **kwargs)

    def log(self, name='session.jsonl', session='session-a', project=None, version='0.167.0'):
        path = self.logs / name
        path.write_text(json.dumps(dict(type='session_meta', payload=dict(id=session, cwd=str(project or self.project), cli_version=version))) + '\n'
                        + json.dumps(dict(type='event_msg', payload=dict(type='note', message='Synthetic'))) + '\n')
        return path

    def test_first_update_stale_head_and_stable_goal(self):
        target = self.inspect(); self.assertIsNone(target['head']); self.assertEqual(target['attachment'], 'unresolved')
        first = self.export(); raw = first.read_bytes(); saved = sf.parse_envelope(raw); report_id = saved['snapshot']['snapshot_id']
        self.assertNotEqual(report_id, 's1'); self.assertEqual(self.inspect()['head'], report_id)
        second = self.export(self.report(report_id), report_id)
        self.assertNotEqual(first, second); self.assertEqual(first.read_bytes(), raw)
        for payload, head in [(self.report(), None), (self.report(report_id), report_id),
                              (dict(self.report(sf.parse_envelope(second.read_bytes())['snapshot']['snapshot_id']), goal_id='wrong'),
                               sf.parse_envelope(second.read_bytes())['snapshot']['snapshot_id'])]:
            with self.assertRaises(ValueError): self.export(payload, head)
        self.assertEqual(len(list(first.parent.glob('*.json'))), 2)

    def test_task_switch_return_null_and_session_separation(self):
        a = self.export(task_ref='docs/a.md'); b = self.export(task_ref='docs/b.md'); self.export()
        self.assertIsNotNone(self.inspect(task_ref='docs/a.md')['head'])
        self.assertNotEqual(self.inspect(task_ref='docs/a.md')['history_id'], self.inspect(task_ref='docs/b.md')['history_id'])
        alias = self.root / 'alias'; alias.symlink_to(self.project)
        self.assertEqual(ex.inspect_target(alias, 'session-a', task_ref='docs/a.md', log_root=self.logs, state_file=self.state)['head'], sf.parse_envelope(a.read_bytes())['snapshot']['snapshot_id'])
        self.assertIsNone(ex.inspect_target(self.project, 'other-session', task_ref='docs/a.md', log_root=self.logs, state_file=self.state)['head'])
        self.assertNotEqual(a, b)

    def test_identity_exact_match_unsupported_and_unchanged_logs(self):
        log = self.log(version='999'); before = hashlib.sha256(log.read_bytes()).hexdigest()
        self.assertEqual(self.inspect()['attachment'], 'metadata-only')
        self.export(); self.assertEqual(hashlib.sha256(log.read_bytes()).hexdigest(), before)
        with self.assertRaisesRegex(ValueError, 'Explicit session'): ex.inspect_target(self.project, '', log_root=self.logs)
        self.log('duplicate.jsonl')
        with self.assertRaisesRegex(ValueError, 'Ambiguous'): self.inspect()
        (self.logs / 'duplicate.jsonl').unlink(); self.log(project=self.root)
        with self.assertRaisesRegex(ValueError, 'directory'): self.inspect()

    def test_manual_binding_requires_current_boundary_and_generation(self):
        self.log(); summary = discover_sessions(self.logs)[0]; cursor = read_delta(summary.path, None).cursor
        generation = f'{cursor.identity}:{cursor.generation}'
        offset = len(summary.path.read_bytes().splitlines(keepends=True)[0])
        boundary = dict(id='manual-context', offset=offset, generation=generation, origin='user-declared', created_at='2026-11-08')
        self.state.write_text(json.dumps(dict(version=1, sessions={summary.id: dict(cursor=asdict(cursor), boundaries=[boundary])})))
        with self.assertRaisesRegex(ValueError, 'explicit context'): self.inspect()
        self.export(context_binding=sf.INITIAL_BINDING)
        binding = dict(version=1, kind='manual', context_id=boundary['id'], generation=generation, boundary_record_key=f'{generation}:{offset}')
        self.export(context_binding=binding)
        wrong = dict(binding, boundary_record_key=f'{generation}:0')
        with self.assertRaisesRegex(ValueError, 'boundary'): self.inspect(context_binding=wrong)
        summary.path.write_text(summary.path.read_text().replace('Synthetic', 'Changed!!'))
        with self.assertRaisesRegex(ValueError, 'stale'): self.inspect(context_binding=binding)

    def test_symlink_escapes_and_log_state_overlap_are_rejected(self):
        folder = self.project / '.session-snapshots'; folder.symlink_to(self.root / 'elsewhere')
        with self.assertRaises(ValueError): self.export()
        folder.unlink()
        with self.assertRaisesRegex(ValueError, 'overlap'): ex.inspect_target(self.project, 'a', log_root=self.project)
        with self.assertRaisesRegex(ValueError, 'overlap'): ex.inspect_target(self.project, 'a', log_root=self.logs, state_file=self.logs / 'state.json')
        with self.assertRaisesRegex(ValueError, 'overlap'): ex.inspect_target(self.project, 'a', log_root=self.logs, state_file=folder / 'state.json')

    def test_default_state_symlink_into_logs_is_rejected(self):
        (self.project / '.local').symlink_to(self.logs)
        with self.assertRaisesRegex(ValueError, 'overlap'):
            ex.inspect_target(self.project, 'a', log_root=self.logs)

    def test_failed_write_flush_and_publication_preserve_prior_files(self):
        first = self.export(); before = first.read_bytes(); head = sf.parse_envelope(before)['snapshot']['snapshot_id']
        for operation in ('write', 'fsync', 'link'):
            with self.subTest(operation=operation), patch(f'viewer.export_snapshot.os.{operation}', side_effect=OSError('injected')):
                with self.assertRaises(ValueError): self.export(self.report(head), head)
            self.assertEqual(first.read_bytes(), before)
            self.assertEqual(list(first.parent.glob('*.json')), [first])
            self.assertEqual(list(first.parent.glob('.tmp-*')), [])

    def test_existing_conflicts_invalid_input_and_safe_generated_paths(self):
        first = self.export(); report = sf.parse_envelope(first.read_bytes()); head = report['snapshot']['snapshot_id']
        duplicate = first.parent / 'duplicate.json'; duplicate.write_text(json.dumps(report, indent=4))
        self.assertEqual(self.inspect()['head'], head)
        report['exported_at'] = '2026-11-08T01:00:00+00:00'; duplicate.write_text(json.dumps(report))
        with self.assertRaisesRegex(ValueError, 'Conflicting'): self.export(self.report(head), head)
        duplicate.unlink(); (first.parent / '.tmp-other-writer').write_text('partial')
        invalid = self.report(head); invalid['snapshot_id'] = '../unsafe'
        with self.assertRaises(ValueError): self.export(invalid, head)
        self.export(self.report(head), head)
        self.assertTrue((first.parent / '.tmp-other-writer').exists())
        self.assertTrue(all(p.name.startswith('report-') for p in first.parent.glob('*.json')))

    def test_git_ignore_preserves_rules_and_rejects_symlink(self):
        (self.project / '.git').mkdir(); ignore = self.project / '.gitignore'; ignore.write_text('keep-me\n')
        self.export(); self.assertEqual(ignore.read_text(), 'keep-me\n/.session-snapshots/\n')
        self.assertTrue(self.inspect()); self.assertEqual(ignore.read_text().count('/.session-snapshots/'), 1)
        ignore.unlink(); outside = self.root / 'ignore'; outside.write_text('untouched\n'); ignore.symlink_to(outside)
        with self.assertRaisesRegex(ValueError, 'symlink'): self.export(self.report(self.inspect()['head']), self.inspect()['head'])
        self.assertEqual(outside.read_text(), 'untouched\n')

    def test_cli_confirmation_errors_inspection_and_concurrent_writers(self):
        input_path = self.root / 'input.json'; input_path.write_text(json.dumps(self.report()))
        base = [sys.executable, '-m', 'viewer.export_snapshot', '--project', str(self.project), '--session-id', 'session-a',
                '--log-root', str(self.logs), '--state-file', str(self.state)]
        cmd = base + ['--input', str(input_path), '--expected-head', 'none']
        processes = [subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) for _ in range(2)]
        outputs = [p.communicate(timeout=15) for p in processes]
        self.assertEqual(sorted(p.returncode for p in processes), [0, 2])
        success = next(out for p, (out, err) in zip(processes, outputs) if p.returncode == 0)
        self.assertIn('Saved', success); self.assertIn('Refresh', success); self.assertIn(str(self.project / '.session-snapshots'), success)
        inspected = subprocess.run(base + ['--inspect'], capture_output=True, text=True)
        self.assertEqual(inspected.returncode, 0); self.assertIsNotNone(json.loads(inspected.stdout)['head'])
        self.assertTrue(any('Another report was saved while this one was being prepared.' in err and 'Run $snapshot-session again' in err for out, err in outputs))
        self.assertEqual(len(list((self.project / '.session-snapshots').glob('*.json'))), 1)


if __name__ == '__main__': unittest.main()
