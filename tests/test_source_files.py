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

    def test_descriptive_and_embedded_file_paths(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'guide.md').write_text('# Decorated source')
            for title in ['guide.md — active progress document',
                          f'User-provided skill and {root}/guide.md']:
                with self.subTest(title=title):
                    text = read_source_file({'sources': [{'id': 's', 'title': title}]}, 's', root)
                    self.assertIn('# Decorated source', text)

    def test_viewer_owned_reference_from_another_project(self):
        path = Path(__file__).resolve().parents[1] / 'skills/snapshot-session/references/report-format.md'
        with tempfile.TemporaryDirectory() as directory:
            text = read_source_file({'sources': [{'id': 's', 'title': str(path)}]}, 's', directory)
            self.assertIn(path.read_text(), text)

    def test_literal_filename_with_spaces_and_description_separator(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            name = 'guide with spaces.md — literal filename'
            (root / name).write_text('Exact filename wins')
            text = read_source_file({'sources': [{'id': 's', 'title': name}]}, 's', root)
            self.assertIn('Exact filename wins', text)

    def test_decorated_paths_still_reject_escape(self):
        with tempfile.TemporaryDirectory() as directory:
            for title in ['/etc/passwd — description', 'Read /etc/passwd',
                          'Read /etc/passwd and /etc/hosts']:
                with self.subTest(title=title), self.assertRaises(ValueError):
                    read_source_file({'sources': [{'id': 's', 'title': title}]}, 's', directory)
