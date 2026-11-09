"""Durable file acceptance, replay and bounded history using synthetic files."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from test_snapshots import sample
from viewer import snapshot_files as sf
from viewer import snapshot_ledger as sl


class SnapshotLedgerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.project = self.root / 'project'; self.project.mkdir()
        self.folder = self.project / '.session-snapshots'; self.folder.mkdir()
        self.state = self.root / 'state.json'
        self.ledger = sl.SnapshotLedger(self.state)

    def report(self, report_id='s1', parent=None, **target):
        return dict(envelope_version=1, project_root=str(self.project), session_id='session-a', task_ref=None,
                    context_binding=sf.INITIAL_BINDING.copy(), exported_at='2026-11-08T00:00:00+00:00',
                    snapshot=sample(report_id, supersedes=parent), **target)

    def save(self, report, name=None):
        path = self.folder / (name or report['snapshot']['snapshot_id'] + '.json')
        path.write_bytes(sf.canonical_bytes(report)); return path

    def collect(self, **options):
        return self.ledger.collect(self.project, **options)

    def history(self, result=None):
        return (result or self.collect())['histories'][0]

    def test_unordered_replay_duplicate_restart_and_source_hashes(self):
        first = self.save(self.report(), 'z.json'); self.save(self.report('s2', 's1'), 'a.json')
        self.save(self.report(), 'duplicate.json')
        hashes = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in self.folder.iterdir()}
        result = self.collect(); view = self.history(result)
        self.assertTrue(result['complete']); self.assertEqual(result['errors'], [])
        self.assertEqual(view['current']['snapshot']['snapshot_id'], 's2'); self.assertEqual(view['count'], 2)
        self.assertFalse(view['current']['retained']); self.assertEqual(view['revision'], 1)
        self.assertEqual(self.history()['revision'], 1)
        restarted = sl.SnapshotLedger(self.state)
        self.assertEqual(restarted.histories(self.project), result['histories'])
        page = restarted.page(self.project, view['history_id'])
        self.assertEqual([r['snapshot']['snapshot_id'] for r in page['entries']], ['s2', 's1'])
        self.assertEqual(page['entries'][1]['source_names'], ['duplicate.json', 'z.json'])
        self.assertEqual(hashes, {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in hashes})
        self.assertEqual(self.ledger.path, self.state.with_suffix('.snapshots.sqlite3'))

    def test_clean_conflicts_accept_only_unambiguous_prefix(self):
        for reports, expected in [([self.report('s2', 'missing')], None),
                                  ([self.report(), self.report('other')], None),
                                  ([self.report(), self.report('s2', 's1'), self.report('s3', 's1')], 's1'),
                                  ([self.report(), self.report('s2', 'missing')], 's1')]:
            with self.subTest(expected=expected):
                for p in self.folder.iterdir(): p.unlink()
                state = self.root / ('state-' + str(len(list(self.root.glob('*.sqlite3')))) + '.json')
                ledger = sl.SnapshotLedger(state)
                for i, report in enumerate(reports): self.save(report, f'{i}.json')
                result = ledger.collect(self.project)
                self.assertTrue(result['errors'])
                current = result['histories'][0]['current']['snapshot']['snapshot_id'] if result['histories'] else None
                self.assertEqual(current, expected)

    def test_conflicting_content_or_metadata_stops_before_id(self):
        for change in ('content', 'metadata'):
            with self.subTest(change=change):
                first = self.report(); other = deepcopy(first)
                if change == 'content': other['snapshot']['goal']['objective'] = 'Different'
                else: other['exported_at'] = '2026-11-08T01:00:00+00:00'
                self.save(first, 'first.json'); self.save(other, 'other.json')
                result = self.collect(); self.assertTrue(result['errors']); self.assertEqual(result['histories'], [])

    def test_late_conflict_preserves_accepted_bytes_and_decision_on_restart(self):
        first = self.report(); self.save(first); accepted = self.history()
        other = deepcopy(first); other['snapshot']['goal']['objective'] = 'Competing'
        self.save(other, 'conflict.json'); self.save(self.report('s2', 's1'))
        result = self.collect(); self.assertTrue(result['errors'])
        self.assertEqual(self.history(result)['current']['snapshot'], first['snapshot'])
        self.assertEqual(self.history(result)['count'], 1)
        restarted = sl.SnapshotLedger(self.state)
        self.assertEqual(restarted.collect(self.project)['histories'][0]['current']['snapshot'], first['snapshot'])
        rebuilt = sl.SnapshotLedger(self.root / 'clean.json').collect(self.project)
        self.assertEqual(rebuilt['histories'], [])
        self.assertNotEqual(accepted, [])

    def test_deleted_mutated_sources_retained_without_relabeling_as_new(self):
        original = self.report(); path = self.save(original); self.collect()
        path.unlink(); retained = self.history()
        self.assertTrue(retained['current']['retained']); self.assertIn('retained', retained['current']['status'].lower())
        self.assertEqual(retained['current']['snapshot'], original['snapshot'])
        changed = deepcopy(original); changed['snapshot']['goal']['objective'] = 'Modified'; self.save(changed)
        result = self.collect(); self.assertTrue(result['errors']); self.assertTrue(self.history(result)['current']['retained'])
        self.save(original); self.assertFalse(self.history()['current']['retained'])

    def test_late_branch_or_root_blocks_new_advancement(self):
        self.save(self.report()); self.save(self.report('s2', 's1')); self.collect()
        self.save(self.report('s3', 's2')); self.save(self.report('branch', 's1'))
        self.assertEqual(self.history()['current']['snapshot']['snapshot_id'], 's2')
        (self.folder / 'branch.json').unlink(); self.save(self.report('other-root'))
        self.assertEqual(self.history()['current']['snapshot']['snapshot_id'], 's2')

    def test_incomplete_or_unreadable_scan_never_commits_partial_prefix(self):
        self.save(self.report()); self.collect(); self.save(self.report('s2', 's1'))
        result = self.collect(max_files=1); self.assertFalse(result['complete'])
        self.assertEqual(self.history(result)['current']['snapshot']['snapshot_id'], 's1')
        self.assertEqual(sl.SnapshotLedger(self.state).histories(self.project)[0]['count'], 1)
        clean = sl.SnapshotLedger(self.root / 'clean.json').collect(self.project, max_bytes=1)
        self.assertEqual(clean['histories'], [])
        original = sf.os.open
        def deny(name, *args, **kwargs):
            if name == 's2.json': raise PermissionError('injected unreadable report')
            return original(name, *args, **kwargs)
        with patch('viewer.snapshot_files.os.open', side_effect=deny):
            result = self.collect()
        self.assertFalse(result['complete']); self.assertEqual(self.history(result)['count'], 1)

    def test_transaction_failure_does_not_publish_or_commit_new_head(self):
        self.save(self.report()); before = self.history()
        self.save(self.report('s2', 's1'))
        connection = sqlite3.connect(self.ledger.path)
        connection.execute("CREATE TRIGGER reject_new BEFORE INSERT ON reports WHEN NEW.report_id = 's2' BEGIN SELECT RAISE(ABORT, 'injected failure'); END")
        connection.commit(); connection.close()
        result = self.collect(); self.assertTrue(result['errors']); self.assertFalse(result['complete'])
        self.assertEqual(result['histories'], [before])
        self.assertEqual(sl.SnapshotLedger(self.state).histories(self.project), [before])

    def test_corrupt_unwritable_and_symlink_ledger_fail_closed(self):
        self.save(self.report()); self.collect()
        self.ledger.path.write_bytes(b'corrupt')
        result = sl.SnapshotLedger(self.state).collect(self.project)
        self.assertFalse(result['complete']); self.assertTrue(result['errors']); self.assertEqual(self.ledger.path.read_bytes(), b'corrupt')
        unwritable = sl.SnapshotLedger(self.root / 'missing' / 'state.json')
        with patch('viewer.snapshot_ledger.sqlite3.connect', side_effect=sqlite3.OperationalError('readonly')):
            result = unwritable.collect(self.project)
        self.assertFalse(result['complete']); self.assertTrue(result['errors'])
        self.ledger.path.unlink(); outside = self.root / 'outside'; outside.write_text('untouched'); self.ledger.path.symlink_to(outside)
        self.assertFalse(sl.SnapshotLedger(self.state).collect(self.project)['complete'])
        self.assertEqual(outside.read_text(), 'untouched')

    def test_report_129_paging_head_independent_and_cursor_revision(self):
        for i in range(1, 130): self.save(self.report(f's{i}', f's{i-1}' if i > 1 else None))
        view = self.history(); hid = view['history_id']
        self.assertEqual(view['count'], 129); self.assertEqual(view['current']['snapshot']['snapshot_id'], 's129')
        first = self.ledger.page(self.project, hid, limit=100); self.assertEqual(len(first['entries']), 100)
        second = self.ledger.page(self.project, hid, cursor=first['next_cursor']); self.assertEqual(len(second['entries']), 29)
        self.assertIsNone(second['next_cursor']); self.assertEqual(second['current']['snapshot']['snapshot_id'], 's129')
        with self.assertRaises(ValueError): self.ledger.page(self.project, hid, limit=101)
        self.save(self.report('s130', 's129')); self.collect()
        with self.assertRaisesRegex(ValueError, 'changed'): self.ledger.page(self.project, hid, cursor=first['next_cursor'])
        with self.assertRaises(ValueError): self.ledger.page(self.project, hid, cursor='invalid')

    def test_storage_never_overlaps_report_files_or_read_only_logs(self):
        self.save(self.report())
        source_before = {p: p.read_bytes() for p in self.folder.iterdir()}
        result = sl.SnapshotLedger(self.folder / 'state.json').collect(self.project)
        self.assertFalse(result['complete'])
        self.assertFalse((self.folder / 'state.snapshots.sqlite3').exists())
        logs = self.root / 'logs'; logs.mkdir()
        result = sl.SnapshotLedger(logs / 'state.json', log_root=logs).collect(self.project)
        self.assertFalse(result['complete']); self.assertEqual(list(logs.iterdir()), [])
        self.assertEqual(source_before, {p: p.read_bytes() for p in self.folder.iterdir()})

    def test_namespace_task_session_and_project_isolation(self):
        a = self.report(); b = deepcopy(a); b['session_id'] = 'session-b'
        c = deepcopy(a); c['task_ref'] = str(self.project / 'docs' / 'progress.md')
        for i, report in enumerate([a,b,c]): self.save(report, f'{i}.json')
        result = self.collect(); self.assertEqual(len(result['histories']), 3)
        other = self.root / 'other'; other.mkdir()
        self.assertEqual(self.ledger.histories(other), [])
        with self.assertRaises(ValueError): self.ledger.page(other, result['histories'][0]['history_id'])

    def test_stored_content_corruption_does_not_replace_decisions(self):
        self.save(self.report()); self.collect()
        connection = sqlite3.connect(self.ledger.path)
        connection.execute("UPDATE reports SET envelope=?", (b'{}',)); connection.commit(); connection.close()
        result = sl.SnapshotLedger(self.state).collect(self.project)
        self.assertFalse(result['complete']); self.assertTrue(result['errors'])
        with self.assertRaises(ValueError): self.ledger.page(self.project, sf.history_key(self.report()))

    def test_page_reads_only_current_head_and_requested_entries(self):
        for i in range(1, 130): self.save(self.report(f's{i}', f's{i-1}' if i > 1 else None))
        view = self.history()
        original = sl.parse_envelope
        with patch('viewer.snapshot_ledger.parse_envelope', wraps=original) as parse:
            page = self.ledger.page(self.project, view['history_id'], limit=1)
        self.assertEqual(len(page['entries']), 1)
        self.assertLessEqual(parse.call_count, 2)

    def test_diagnostics_are_bounded_and_invalid_candidates_consume_budget(self):
        for i in range(110): (self.folder / f'{i}.json').write_text('bad')
        result = self.collect(); self.assertEqual(result['error_count'], 110); self.assertLessEqual(len(result['errors']), 100)
        self.assertTrue(result['complete']); self.assertEqual(result['histories'], [])
        self.assertFalse(self.collect(max_files=100)['complete'])
        self.assertLess(self.ledger.path.stat().st_size, 100000)


if __name__ == '__main__': unittest.main()
