from pathlib import Path
import tempfile
import unittest
from viewer.source_files import read_source_file

class SourceFileTests(unittest.TestCase):
    def test_current_markdown_not_captured_excerpt(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'guide.md').write_text('# Current guide\nUpdated content', encoding='utf-8')
            view = {'sources': [{'id': 'guide', 'title': 'guide.md', 'matches': [{'text': 'old excerpt'}]}]}
            text = read_source_file(view, 'guide', root)
            self.assertIn('# Current guide\nUpdated content', text)
            self.assertIn('Current file', text)
            self.assertNotIn('old excerpt', text)

    def test_rejects_unreferenced_missing_and_outside_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for title in ['../outside.md', '/etc/passwd', 'missing.md']:
                with self.assertRaises(ValueError):
                    read_source_file({'sources': [{'id': 's', 'title': title}]}, 's', root)
            with self.assertRaises(ValueError):
                read_source_file({'sources': []}, 'arbitrary', root)

    def test_rejects_symlink_escape_binary_and_large_file(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'escape.md').symlink_to('/etc/passwd')
            (root / 'binary.md').write_bytes(b'\x00binary')
            (root / 'large.md').write_bytes(b'a' * (1024 * 1024 + 1))
            for title in ['escape.md', 'binary.md', 'large.md']:
                with self.assertRaises(ValueError):
                    read_source_file({'sources': [{'id': 's', 'title': title}]}, 's', root)
