"""Synthetic export-envelope and deterministic-chain contracts."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from test_snapshots import sample
from viewer import snapshot_files as sf


class SnapshotFileTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()

    def envelope(self, report_id='s1', parent=None):
        return dict(envelope_version=1, project_root=str(self.root), session_id='session-a', task_ref=None,
                    context_binding=sf.INITIAL_BINDING.copy(), exported_at='2026-11-08T00:00:00+00:00',
                    snapshot=sample(report_id, supersedes=parent))

    def test_envelope_strict_encoding_fields_depth_and_full_goal(self):
        value = self.envelope()
        self.assertEqual(sf.parse_envelope(json.dumps(value).encode()), value)
        bad = [b'\xff', b'{"a":1,"a":2}', b'{"a":NaN}', b'[' * 65 + b'0' + b']' * 65]
        for field, replacement in [('envelope_version', True), ('task_ref', 'relative'),
                                   ('session_id', ''), ('project_root', '/x/../y'),
                                   ('context_binding', {'version': 2, 'kind': 'initial'})]:
            other = deepcopy(value); other[field] = replacement; bad.append(json.dumps(other).encode())
        other = deepcopy(value); other['extra'] = 1; bad.append(json.dumps(other).encode())
        other = self.envelope(); other['snapshot'] = sample('s1', 'a'); bad.append(json.dumps(other).encode())
        other = self.envelope(); other['snapshot']['goal']['objective'] = '\ud800'; bad.append(json.dumps(other).encode())
        bad.append(b' ' * (sf.MAX_ENVELOPE_BYTES + 1))
        for raw in bad:
            with self.subTest(raw=raw[:80]), self.assertRaises(ValueError): sf.parse_envelope(raw)

    def test_canonical_duplicates_collapse_but_metadata_variants_conflict(self):
        first = self.envelope(); second = self.envelope('s2', 's1')
        self.assertEqual(sf.chain_head([second, first, deepcopy(first)])['snapshot']['snapshot_id'], 's2')
        self.assertEqual(sf.canonical_bytes(first), sf.canonical_bytes(dict(reversed(list(first.items())))))
        other = deepcopy(first); other['exported_at'] = '2026-11-08T01:00:00+00:00'
        with self.assertRaisesRegex(ValueError, 'Conflicting'): sf.chain_head([first, other])

    def test_chain_rejects_missing_parents_branches_roots_goal_changes(self):
        first = self.envelope()
        variants = [[self.envelope('s2', 'missing')], [first, self.envelope('s2')],
                    [first, self.envelope('s2', 's1'), self.envelope('s3', 's1')]]
        changed = self.envelope('s2', 's1'); changed['snapshot']['goal_id'] = 'different'
        variants.append([first, changed])
        for reports in variants:
            with self.assertRaises(ValueError): sf.chain_head(reports)

    def test_scan_directory_swap_cannot_follow_an_escaping_parent_symlink(self):
        folder = self.root / '.session-snapshots'; folder.mkdir()
        outside = self.root / 'outside'; outside.mkdir()
        (outside / 'escaped.json').write_text(json.dumps(self.envelope()))
        original = sf.os.scandir
        def swapped(path):
            folder.rename(self.root / 'old-folder'); folder.symlink_to(outside)
            return original(path)
        with patch('viewer.snapshot_files.os.scandir', side_effect=swapped):
            scanned = sf.scan_reports(self.root)
        self.assertFalse(scanned.complete)
        self.assertEqual(scanned.reports, [])

    def test_publication_during_scan_is_incomplete(self):
        folder = self.root / '.session-snapshots'; folder.mkdir()
        (folder / 'a.json').write_text(json.dumps(self.envelope()))
        original = sf.parse_envelope
        def publish(raw):
            (folder / 'b.json').write_text(json.dumps(self.envelope('s2', 's1')))
            return original(raw)
        with patch('viewer.snapshot_files.parse_envelope', side_effect=publish):
            self.assertFalse(sf.scan_reports(self.root).complete)

    def test_hidden_invalid_json_counts_but_writer_temporary_is_ignored(self):
        folder = self.root / '.session-snapshots'; folder.mkdir()
        (folder / '.invalid.json').write_text('bad')
        (folder / '.tmp-writer.json').write_text('partial')
        result = sf.scan_reports(self.root)
        self.assertEqual(result.count, 1); self.assertEqual(result.error_count, 1)

    def test_project_task_canonicalization_and_history_isolation(self):
        alias = self.root / 'alias'; project = self.root / 'project'; project.mkdir(); alias.symlink_to(project)
        self.assertEqual(sf.canonical_project(alias), project)
        self.assertEqual(sf.canonical_task(project, 'docs/progress.md'), str(project / 'docs/progress.md'))
        self.assertIsNone(sf.canonical_task(project, None))
        first = self.envelope()
        for key, val in [('session_id', 'session-b'), ('task_ref', str(self.root / 'progress.md')),
                         ('context_binding', dict(version=1, kind='manual', context_id='ctx', generation='dev:0', boundary_record_key='dev:0:2'))]:
            other = deepcopy(first); other[key] = val
            self.assertNotEqual(sf.history_key(first), sf.history_key(other))

    def test_scan_ignores_temporary_rejects_symlink_and_bounds_candidates(self):
        folder = self.root / '.session-snapshots'; folder.mkdir()
        (folder / '.tmp-writer').write_text('partial')
        (folder / 'a.json').write_text(json.dumps(self.envelope()))
        scanned = sf.scan_reports(self.root)
        self.assertTrue(scanned.complete); self.assertFalse(scanned.errors); self.assertEqual(len(scanned.reports), 1)
        outside = self.root / 'outside'; outside.write_text('{}'); (folder / 'b.json').symlink_to(outside)
        self.assertTrue(sf.scan_reports(self.root).errors)
        self.assertFalse(sf.scan_reports(self.root, max_files=1).complete)
        self.assertFalse(sf.scan_reports(self.root, max_bytes=1).complete)
        self.assertEqual(sf.MAX_FILES, 10000); self.assertEqual(sf.MAX_SCAN_BYTES, 256 * 1024 * 1024)


if __name__ == '__main__': unittest.main()
