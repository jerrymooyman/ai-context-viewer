"""Export one immutable complete self-report; never write to Codex logs.

Inspect first, then submit semantic schema-v1 fields (all except snapshot_id)
with the inspected expected head. The helper generates the immutable report ID.
"""
import argparse
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import stat
import sys
import uuid

from .logs import Cursor, discover_sessions, read_delta
from .snapshot_files import (INITIAL_BINDING, MAX_ENVELOPE_BYTES, canonical_bytes, canonical_project,
                             canonical_task, chain_head, history_key, scan_reports,
                             snapshot_directory, strict_json, validate_binding, validate_envelope)
from .snapshots import MAX_SNAPSHOT_BYTES, identifier, require
from .state import State


def _paths(project, log_root=None, state_file=None):
    project = canonical_project(project)
    logs = Path(log_root if log_root is not None else Path(os.environ.get('CODEX_HOME', Path.home() / '.codex')) / 'sessions').resolve()
    state_path = (Path(state_file) if state_file is not None else project / '.local' / 'state.json').resolve()
    directory = snapshot_directory(project)
    def overlap(left, right):
        return left.is_relative_to(right) or right.is_relative_to(left)
    require(not overlap(directory, logs) and not overlap(state_path, logs) and not overlap(state_path, directory),
            'State/export paths must not overlap logs or each other.')
    require(state_path.suffix != '.jsonl', 'State file cannot be a JSONL log.')
    return project, logs, state_path, directory


def _route(project, session_id, task_ref, context_binding, log_root, state_file):
    require(isinstance(session_id, str) and 0 < len(session_id) <= 256 and not any(ord(c) < 32 for c in session_id),
            'Explicit session identity is required; provide --session-id from the current client. No newest-session fallback.')
    state = State(state_file)
    require(not state.warning, state.warning)
    matches = [s for s in discover_sessions(log_root) if s.session_id == session_id]
    require(len(matches) <= 1, 'Ambiguous recorded session identity; resolve duplicate metadata before exporting.')
    summary = matches[0] if matches else None
    if summary:
        require(summary.project and Path(summary.project).is_absolute() and Path(summary.project).resolve() == project,
                'Recorded session directory does not match the exact canonical project directory.')
    binding = deepcopy(context_binding if context_binding is not None else INITIAL_BINDING)
    validate_binding(binding)
    saved = state.data['sessions'].get(summary.id, {}) if summary else {}
    boundaries = saved.get('boundaries', [])
    require(not boundaries or context_binding is not None,
            'Viewer boundaries exist; provide an explicit context binding (including the initial target).')
    attachment = 'unresolved' if summary is None else ('attached' if summary.status == 'supported' else 'metadata-only')
    if binding['kind'] == 'manual':
        require(summary is not None, 'Manual boundary cannot be validated without a uniquely matching log.')
        saved_cursor = saved.get('cursor')
        require(saved_cursor is not None, 'Manual boundary state is unavailable.')
        checked = read_delta(summary.path, Cursor(**saved_cursor)).cursor
        generation = f'{checked.identity}:{checked.generation}'
        require(binding['generation'] == generation, 'Manual context generation is stale; copy a current target from the viewer.')
        boundary = next((b for b in boundaries if b['id'] == binding['context_id'] and b['generation'] == generation), None)
        require(boundary is not None and binding['boundary_record_key'] == f"{generation}:{boundary['offset']}",
                'Manual boundary is missing or does not match the boundary record key.')
    return dict(envelope_version=1, project_root=str(project), session_id=session_id,
                task_ref=canonical_task(project, task_ref), context_binding=binding), attachment


def _head(project, target):
    scan = scan_reports(project)
    require(scan.complete, 'Snapshot collection incomplete; existing reports were not changed. ' + '; '.join(scan.errors))
    require(not scan.errors, 'Snapshot collection has invalid reports; existing reports were not changed. ' + '; '.join(scan.errors))
    reports = [r for r in scan.reports if history_key(r) == history_key(target)]
    return chain_head(reports)


def _target_view(target, attachment, head):
    return dict(target, history_id=history_key(target), attachment=attachment,
                head=head['snapshot']['snapshot_id'] if head else None,
                goal_id=head['snapshot']['goal_id'] if head else None)


def inspect_target(project, session_id, *, task_ref=None, context_binding=None, log_root=None, state_file=None):
    project, logs, state, _ = _paths(project, log_root, state_file)
    target, attachment = _route(project, session_id, task_ref, context_binding, logs, state)
    return _target_view(target, attachment, _head(project, target))


@contextmanager
def _directory_lock(directory, name):
    """No-follow directory and lock handles; OS locks release on process exit."""
    directory.mkdir(mode=0o700, exist_ok=True)
    directory_fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    lock_fd = None
    try:
        lock_fd = os.open(name, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600, dir_fd=directory_fd)
        require(stat.S_ISREG(os.fstat(lock_fd).st_mode), 'Export lock must be a regular file.')
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        require(directory.stat().st_ino == os.fstat(directory_fd).st_ino and not directory.is_symlink(),
                'Snapshot destination changed while acquiring lock.')
        yield directory_fd
    finally:
        if lock_fd is not None:
            os.close(lock_fd)
        os.close(directory_fd)


def _ensure_ignore(project):
    # Parent Git repositories also need this precise project-local rule.
    if not any((parent / '.git').exists() for parent in [project, *project.parents]):
        return
    path = project / '.gitignore'
    require(not path.is_symlink(), '.gitignore must not be a symlink; add /.session-snapshots/ to a regular ignore file.')
    fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o644)
    try:
        require(stat.S_ISREG(os.fstat(fd).st_mode), '.gitignore must be a regular file.')
        fcntl.flock(fd, fcntl.LOCK_EX)
        with os.fdopen(fd, 'r+b', closefd=False) as stream:
            existing = stream.read(1024 * 1024 + 1)
            require(len(existing) <= 1024 * 1024, '.gitignore exceeds 1 MiB; add the snapshot rule manually.')
            # Put the rule last so earlier negations cannot override it.
            if existing.splitlines() and existing.splitlines()[-1] == b'/.session-snapshots/':
                return
            stream.seek(0, os.SEEK_END)
            stream.write((b'\n' if existing and not existing.endswith(b'\n') else b'') + b'/.session-snapshots/\n')
            stream.flush(); os.fsync(fd)
    finally:
        os.close(fd)


def _publish(directory_fd, encoded):
    token = uuid.uuid4().hex
    temporary = '.tmp-' + token
    final = 'report-' + token + '.json'
    created = False
    try:
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=directory_fd)
        created = True
        try:
            view = memoryview(encoded)
            while view:
                written = os.write(fd, view)
                require(written > 0, 'Export write failed.')
                view = view[written:]
            os.fsync(fd)
        finally:
            os.close(fd)
        os.link(temporary, final, src_dir_fd=directory_fd, dst_dir_fd=directory_fd, follow_symlinks=False)
        try:
            os.fsync(directory_fd)
        except OSError as error:
            raise ValueError(f'Report published as {final}, but directory durability could not be confirmed; inspect before retrying.') from error
        return final
    finally:
        if created:
            os.unlink(temporary, dir_fd=directory_fd)


def export_snapshot(project, session_id, payload, *, expected_head, task_ref=None, context_binding=None,
                    log_root=None, state_file=None):
    """Publish full semantic input under a history lock, rejecting stale heads.

    Input contains exactly schema-v1 fields except snapshot_id; goal_id and
    supersedes are caller-reviewed semantic ancestry, never silently rewritten.
    """
    try:
        project, logs, state, directory = _paths(project, log_root, state_file)
        target, _ = _route(project, session_id, task_ref, context_binding, logs, state)
        require(expected_head is None or identifier(expected_head), 'Expected head must be null or a snapshot ID.')
        require(isinstance(payload, dict) and set(payload) == {'schema_version', 'goal_id', 'scope', 'supersedes', 'goal', 'subtasks', 'claims', 'sources'},
                'Input requires exactly schema v1 fields except snapshot_id; the helper generates the report ID.')
        # Round-trip strict encoding checks depth, Unicode and the 64 KiB input bound.
        payload = strict_json(canonical_bytes(payload), MAX_SNAPSHOT_BYTES)
        with _directory_lock(directory, '.lock-' + history_key(target)) as directory_fd:
            # Recheck identity/boundaries and complete ancestry after acquiring lock.
            current_target, _ = _route(project, session_id, task_ref, context_binding, logs, state)
            require(target == current_target, 'Target changed while acquiring the export lock; inspect again.')
            head = _head(project, target)
            current_id = head['snapshot']['snapshot_id'] if head else None
            require(expected_head == current_id, 'Another report was saved while this one was being prepared. Run $snapshot-session again to save an updated report.')
            require(payload['supersedes'] == current_id, 'Report supersedes must match the inspected current head.')
            if head:
                require(payload['goal_id'] == head['snapshot']['goal_id'], 'Goal ID cannot change within a file history.')
            snapshot = dict(payload, snapshot_id='report-' + uuid.uuid4().hex)
            envelope = dict(target, exported_at=datetime.now(timezone.utc).isoformat(), snapshot=snapshot)
            validate_envelope(envelope)
            _ensure_ignore(project)
            filename = _publish(directory_fd, canonical_bytes(envelope))
            return directory / filename
    except OSError as error:
        raise ValueError('Export filesystem operation failed; prior reports were not changed. Check destination permissions and retry after inspection.') from error


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project', type=Path, default=Path.cwd(), help='Exact project root (defaults to caller directory).')
    parser.add_argument('--session-id', required=True, help='Explicit current client identity; never inferred from newest log or environment.')
    parser.add_argument('--task-ref', help='Explicit progress-document path; omission means Unassigned task.')
    parser.add_argument('--context-binding', help='Explicit version-1 binding JSON; required when viewer boundaries exist.')
    parser.add_argument('--log-root', type=Path, help='Read-only session metadata root (default CODEX_HOME/sessions).')
    parser.add_argument('--state-file', type=Path, help='Viewer boundary sidecar (default PROJECT/.local/state.json).')
    parser.add_argument('--inspect', action='store_true', help='Print target/head JSON without exporting.')
    parser.add_argument('--input', type=Path, help='Semantic schema-v1 JSON file, omitting snapshot_id; use - for stdin.')
    parser.add_argument('--expected-head', help='Inspected head ID, or none for a first report; required for export.')
    args = parser.parse_args(argv)
    try:
        binding = strict_json(args.context_binding) if args.context_binding is not None else None
        options = dict(task_ref=args.task_ref, context_binding=binding, log_root=args.log_root, state_file=args.state_file)
        if args.inspect:
            require(args.input is None and args.expected_head is None, '--inspect cannot be combined with export arguments.')
            print(json.dumps(inspect_target(args.project, args.session_id, **options), ensure_ascii=False, sort_keys=True))
        else:
            require(args.input is not None and args.expected_head is not None, 'Export requires --input and --expected-head (ID or none); inspect first.')
            if str(args.input) == '-':
                raw = sys.stdin.buffer.read(MAX_SNAPSHOT_BYTES + 1)
            else:
                with args.input.open('rb') as stream: raw = stream.read(MAX_SNAPSHOT_BYTES + 1)
            payload = strict_json(raw, MAX_SNAPSHOT_BYTES)
            destination = export_snapshot(args.project, args.session_id, payload,
                                          expected_head=None if args.expected_head == 'none' else args.expected_head, **options)
            print(f'Saved {destination}. Press Refresh in ai-context-viewer to collect this agent self-report.')
        return 0
    except (ValueError, OSError) as error:
        print(f'Export error: {error}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
