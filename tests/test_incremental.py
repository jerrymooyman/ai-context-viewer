import json
from pathlib import Path
import tempfile
import unittest
from viewer.logs import read_delta


class IncrementalTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'log.jsonl'

    def test_truncate_regrow_past_offset_resets_generation(self):
        self.path.write_bytes(b'{"old":1}\n')
        first = read_delta(self.path, None)
        self.path.write_bytes(b'{"new":2}\n' * 5)
        second = read_delta(self.path, first.cursor)
        self.assertGreater(second.cursor.generation, first.cursor.generation)
        self.assertEqual(second.records[0].record, {'new': 2})
        self.assertEqual(second.records[0].key.offset, 0)

    def test_split_utf8_and_equal_records_at_distinct_positions(self):
        row = json.dumps({'text': '🦜'}, ensure_ascii=False).encode() + b'\n'
        self.path.write_bytes(row[:-3])
        first = read_delta(self.path, None)
        self.assertEqual(first.records, [])
        self.assertEqual(first.warnings, [])
        with self.path.open('ab') as stream:
            stream.write(row[-3:] + row)
        second = read_delta(self.path, first.cursor)
        self.assertEqual(len(second.records), 2)
        self.assertNotEqual(second.records[0].key, second.records[1].key)
        self.assertEqual(read_delta(self.path, second.cursor).records, [])

    def test_oversized_line_does_not_hide_later_records(self):
        self.path.write_bytes(b'x' * (1024 * 1024 + 5) + b'\n{"ok":1}\n')
        batch = read_delta(self.path, None)
        self.assertEqual([r.record for r in batch.records], [{'ok': 1}])
        self.assertTrue(batch.warnings)
