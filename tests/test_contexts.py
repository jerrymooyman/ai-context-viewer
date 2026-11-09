import json
from pathlib import Path
import tempfile
import unittest
from viewer import contexts
from viewer.logs import discover_sessions


class ContextTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.path = self.root / 'sample.jsonl'
        rows = [dict(type='session_meta', payload=dict(id='s', cli_version='0.167.0'))]
        for text in ['first', 'handoff']:
            rows.append(dict(type='response_item', payload=dict(type='message', role='user', content=[dict(text=text)])))
        self.path.write_text(''.join(json.dumps(r) + '\n' for r in rows))
        self.summary = discover_sessions(self.root)[0]
        self.state_path = self.root / 'state.json'

    def store(self):
        return contexts.ContextStore(self.root, self.state_path)

    def test_boundary_separates_handoff_and_survives_restart(self):
        store = self.store()
        before = self.path.read_bytes()
        view = store.view(self.summary, self.summary.id)
        key = view['supplied'][-1]['key']
        boundary = store.declare(self.summary, self.summary.id, key)
        self.assertEqual([r['text'] for r in store.view(self.summary, self.summary.id)['supplied']], ['first'])
        second = self.store().view(self.summary, boundary)
        self.assertEqual([r['text'] for r in second['supplied']], ['handoff'])
        self.assertEqual(second['boundary']['origin'], 'user-declared')
        self.assertEqual(self.path.read_bytes(), before)
        store.remove(self.summary, boundary)
        self.assertEqual(len(store.view(self.summary, self.summary.id)['supplied']), 2)

    def test_replacement_does_not_apply_old_boundaries(self):
        store = self.store()
        view = store.view(self.summary, self.summary.id)
        old_id = store.declare(self.summary, self.summary.id, view['supplied'][-1]['key'])
        self.path.write_text(self.path.read_text().replace('first', 'other'))
        current = self.store()
        view = current.view(self.summary, self.summary.id)
        self.assertTrue(any('stale' in w.lower() for w in view['warnings']))
        self.assertNotIn(old_id, [c['id'] for c in current.list_contexts(self.summary)])
        self.assertEqual(len(view['supplied']), 2)

    def test_failed_write_and_corruption_are_visible(self):
        store = contexts.ContextStore(self.root, self.root)  # directory cannot be a state file
        view = store.view(self.summary, self.summary.id)
        self.assertTrue(any('state' in w.lower() for w in view['warnings']))
        with self.assertRaises(ValueError):
            store.declare(self.summary, self.summary.id, view['supplied'][-1]['key'])
        self.assertEqual(len(store.list_contexts(self.summary)), 1)

    def test_pages_and_incremental_append(self):
        store = self.store()
        view = store.view(self.summary, self.summary.id, after=0, limit=1)
        self.assertEqual(len(view['supplied']) + len(view['activity']), 1)
        self.assertIsNotNone(view['next'])
        with self.path.open('a') as stream:
            stream.write('{"type":"event_msg","payload":{"type":"task_started"}}\n')
        newer = store.view(self.summary, self.summary.id)
        self.assertNotEqual(view['revision'], newer['revision'])
        self.assertEqual(len(newer['activity']), 1)
        self.assertEqual(len(store.view(self.summary, self.summary.id)['supplied']), 2)

    def test_real_write_failure_keeps_boundary_unchanged(self):
        blocked = self.root / 'blocked'
        blocked.write_text('not a directory')
        store = contexts.ContextStore(self.root, blocked / 'state.json')
        view = store.view(self.summary, self.summary.id)
        with self.assertRaisesRegex(ValueError, 'write failed'):
            store.declare(self.summary, self.summary.id, view['supplied'][-1]['key'])
        self.assertEqual(len(store.list_contexts(self.summary)), 1)

    def test_state_path_cannot_replace_source_log(self):
        store = contexts.ContextStore(self.root, self.path)
        before = self.path.read_bytes()
        with self.assertRaises(ValueError):
            store.state.save({'version': 1, 'sessions': {}})
        self.assertEqual(self.path.read_bytes(), before)

    def test_two_sessions_remain_isolated_and_empty_context_inherits_nothing(self):
        other = self.root / 'other.jsonl'
        other.write_text(self.path.read_text().splitlines()[0] + '\n')
        summary = next(s for s in discover_sessions(self.root) if s.path == other)
        store = self.store()
        self.assertEqual(store.view(summary, summary.id)['supplied'], [])
        first = store.view(self.summary, self.summary.id)
        store.declare(self.summary, self.summary.id, first['supplied'][-1]['key'])
        self.assertEqual(len(store.list_contexts(summary)), 1)
        self.assertEqual(store.view(summary, summary.id)['activity'], [])

    def test_idle_is_distinct_from_unavailable_and_unsupported(self):
        store = self.store()
        store.view(self.summary, self.summary.id)
        idle = store.view(self.summary, self.summary.id)
        self.assertEqual(idle['collection_status'], 'idle')
        self.assertEqual(idle['status'], 'supported')
        self.path.unlink()
        with self.assertRaises(OSError):
            store.view(self.summary, self.summary.id)

    def test_checkpoint_state_with_extra_cursor_fields_is_rejected(self):
        store = self.store()
        view = store.view(self.summary, self.summary.id)
        store.declare(self.summary, self.summary.id, view['supplied'][-1]['key'])
        data = json.loads(self.state_path.read_text())
        data['sessions'][self.summary.id]['cursor']['unexpected'] = 1
        self.state_path.write_text(json.dumps(data))
        repaired = self.store().view(self.summary, self.summary.id)
        self.assertTrue(any('corrupt' in w.lower() for w in repaired['warnings']))
