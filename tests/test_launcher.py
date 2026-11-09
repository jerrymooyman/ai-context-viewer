"""Disposable launcher/install journeys; never touch user installation roots."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import shutil
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class LauncherTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='viewer packaging ')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.projects = [self.root / 'project one', self.root / 'project two']
        for project in self.projects:
            project.mkdir()
        self.bin = self.root / 'user bin'
        self.skills = self.root / 'user skills'
        self.logs = self.root / 'logs'; self.logs.mkdir()

    def run_launcher(self, *args, cwd=None, installed=False):
        command = [str(self.bin / 'ai-context-viewer')] if installed else [sys.executable, str(ROOT / 'scripts/ai-context-viewer')]
        return subprocess.run(command + list(args), cwd=cwd or self.projects[0], capture_output=True, text=True, timeout=15)

    def install(self, *args):
        return subprocess.run([sys.executable, str(ROOT / 'scripts/install-user'), '--bin-dir', str(self.bin),
                               '--skills-dir', str(self.skills), *args], capture_output=True, text=True, timeout=15)

    def test_caller_roots_and_project_override(self):
        for project in self.projects:
            result = self.run_launcher('--export-snapshot', '--inspect', '--session-id', 'explicit-id', '--log-root', str(self.logs), cwd=project)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout)['project_root'], str(project.resolve()))
        result = self.run_launcher('--export-snapshot', '--inspect', '--project', str(self.projects[1]), '--session-id', 'explicit-id', '--log-root', str(self.logs))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)['project_root'], str(self.projects[1].resolve()))
        self.assertEqual(self.run_launcher('--help').returncode, 0)

    def test_install_requires_identity_gate_then_idempotent_and_exact_uninstall(self):
        blocked = self.install()
        self.assertEqual(blocked.returncode, 2)
        self.assertIn('identity', blocked.stderr.lower())
        self.assertFalse(self.bin.exists())
        first = self.install('--identity-verified')
        self.assertEqual(first.returncode, 0, first.stderr)
        command = self.bin / 'ai-context-viewer'
        before = command.read_bytes()
        self.assertIn(os.fsencode(sys.executable), before)
        self.assertEqual(self.install('--identity-verified').returncode, 0)
        self.assertEqual(command.read_bytes(), before)
        self.assertEqual((self.skills / 'snapshot-session').resolve(), ROOT / 'skills/snapshot-session')
        self.assertEqual(self.run_launcher('--help', installed=True).returncode, 0)
        inspected = self.run_launcher('--export-snapshot', '--inspect', '--session-id', 'explicit-id', '--log-root', str(self.logs), cwd=self.projects[1], installed=True)
        self.assertEqual(inspected.returncode, 0, inspected.stderr)
        self.assertEqual(json.loads(inspected.stdout)['project_root'], str(self.projects[1].resolve()))
        removed = self.install('--uninstall')
        self.assertEqual(removed.returncode, 0, removed.stderr)
        self.assertIn(str(command), removed.stdout)
        self.assertFalse(command.exists()); self.assertFalse((self.skills / 'snapshot-session').is_symlink())
        self.assertEqual(self.install('--uninstall').returncode, 0)
        self.assertTrue((ROOT / 'skills/snapshot-session/SKILL.md').is_file())

    def test_collisions_never_overwrite_or_partially_install(self):
        self.bin.mkdir(); command = self.bin / 'ai-context-viewer'; command.write_text('unrelated command')
        self.assertEqual(self.install('--identity-verified').returncode, 2)
        self.assertEqual(command.read_text(), 'unrelated command')
        self.assertFalse((self.skills / 'snapshot-session').exists())
        command.unlink(); self.skills.mkdir(); skill = self.skills / 'snapshot-session'; skill.mkdir()
        (skill / 'keep').write_text('unrelated skill')
        self.assertEqual(self.install('--identity-verified').returncode, 2)
        self.assertFalse(command.exists()); self.assertEqual((skill / 'keep').read_text(), 'unrelated skill')

    def test_uninstall_refuses_modified_or_unrelated_destinations(self):
        self.assertEqual(self.install('--identity-verified').returncode, 0)
        command = self.bin / 'ai-context-viewer'; command.write_text('changed')
        self.assertEqual(self.install('--uninstall').returncode, 2)
        self.assertEqual(command.read_text(), 'changed')
        self.assertTrue((self.skills / 'snapshot-session').is_symlink())

    def test_explicit_skill_and_valid_format_example(self):
        policy = ROOT / 'skills/snapshot-session/agents/openai.yaml'
        self.assertTrue(policy.is_file())
        self.assertIn('allow_implicit_invocation: false', policy.read_text())
        from viewer.snapshot_files import parse_envelope
        path = ROOT / 'skills/snapshot-session/references/envelope-example.json'
        report = parse_envelope(path.read_bytes())
        self.assertEqual(report['snapshot']['scope'], 'goal')
        self.assertIsNone(report['snapshot']['supersedes'])

    def test_source_path_with_spaces_and_shared_default_sidecar(self):
        checkout = self.root / "checkout with spaces and ' quote"
        shutil.copytree(ROOT / 'scripts', checkout / 'scripts')
        (checkout / 'viewer').mkdir()
        (checkout / 'viewer/__init__.py').write_text('')
        (checkout / 'viewer/__main__.py').write_text('')
        (checkout / 'viewer/export_snapshot.py').write_text(
            'import json, os, sys; print(json.dumps(dict(cwd=os.getcwd(), args=sys.argv[1:])))')
        shutil.copytree(ROOT / 'skills', checkout / 'skills')
        result = subprocess.run([sys.executable, str(checkout / 'scripts/install-user'), '--identity-verified',
                                 '--bin-dir', str(self.bin), '--skills-dir', str(self.skills)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        result = self.run_launcher('--export-snapshot', '--inspect', installed=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        view = json.loads(result.stdout)
        self.assertEqual(view['cwd'], str(self.projects[0]))
        self.assertEqual(view['args'], ['--inspect', '--state-file', str(checkout / '.local/state.json')])
        custom = self.run_launcher('--export-snapshot', '--state-file=custom.json', installed=True)
        self.assertEqual(json.loads(custom.stdout)['args'], ['--state-file=custom.json'])

    def test_helper_errors_are_forwarded_without_log_writes(self):
        fixture = self.logs / 'sample.jsonl'; fixture.write_text('{}\n')
        before = hashlib.sha256(fixture.read_bytes()).digest()
        result = self.run_launcher('--export-snapshot', '--inspect', '--log-root', str(self.logs))
        self.assertEqual(result.returncode, 2)
        self.assertIn('--session-id', result.stderr)
        self.assertEqual(hashlib.sha256(fixture.read_bytes()).digest(), before)


if __name__ == '__main__':
    unittest.main()
