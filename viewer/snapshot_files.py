"""Strict file envelopes and bounded, read-only ancestry inspection.

Envelope v1 is independent of log/import history. Times never choose a head.
"""
from dataclasses import dataclass, field
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import stat

from .snapshots import validate_snapshot, require, identifier

MAX_ENVELOPE_BYTES = 96 * 1024
MAX_FILES = 10000
MAX_SCAN_BYTES = 256 * 1024 * 1024
MAX_DEPTH = 64
INITIAL_BINDING = {'version': 1, 'kind': 'initial'}
ENVELOPE_FIELDS = {'envelope_version', 'project_root', 'session_id', 'task_ref', 'context_binding', 'exported_at', 'snapshot'}


def canonical_project(path):
    project = Path(path).resolve(strict=True)
    require(project.is_dir(), 'Project must be an existing directory.')
    return project


def canonical_task(project, task_ref):
    if task_ref is None:
        return None
    require(isinstance(task_ref, str) and bool(task_ref) and len(task_ref) <= 8192, 'Task reference must be an explicit path or null.')
    path = Path(task_ref)
    return str((path if path.is_absolute() else Path(project) / path).resolve())


def canonical_bytes(value):
    try:
        return json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(',', ':')).encode('utf-8')
    except (ValueError, TypeError, RecursionError, UnicodeError) as error:
        raise ValueError('JSON encoding or nesting is invalid.') from error


def strict_json(raw, limit=MAX_ENVELOPE_BYTES):
    try:
        if isinstance(raw, str):
            raw = raw.encode('utf-8')
        require(len(raw) <= limit, f'JSON exceeds {limit} bytes.')
        def pairs(items):
            result = {}
            for key, value in items:
                require(key not in result, f'Duplicate JSON key: {key}')
                result[key] = value
            return result
        def constant(_):
            raise ValueError('Nonfinite JSON numbers are unsupported.')
        value = json.loads(raw.decode('utf-8'), object_pairs_hook=pairs, parse_constant=constant)
        def depth(item, level=0):
            require(level <= MAX_DEPTH, 'JSON nesting exceeds 64 levels.')
            if isinstance(item, dict):
                for key, child in item.items():
                    key.encode('utf-8'); depth(child, level + 1)
            elif isinstance(item, list):
                for child in item: depth(child, level + 1)
            elif isinstance(item, str):
                item.encode('utf-8')
        depth(value)
        canonical_bytes(value)  # Also rejects exponent overflow such as 1e999.
        return value
    except (UnicodeError, RecursionError) as error:
        raise ValueError('JSON encoding or nesting is invalid.') from error


def validate_binding(binding):
    require(isinstance(binding, dict) and type(binding.get('version')) is int and binding['version'] == 1,
            'Unsupported context binding; expected version 1.')
    if binding.get('kind') == 'initial':
        require(binding == INITIAL_BINDING, 'Initial binding requires exactly version and kind.')
    else:
        require(set(binding) == {'version', 'kind', 'context_id', 'generation', 'boundary_record_key'}
                and binding.get('kind') == 'manual', 'Invalid manual context binding fields.')
        require(identifier(binding['context_id']) and all(isinstance(binding[k], str) and 0 < len(binding[k]) <= 256
                for k in ('generation', 'boundary_record_key')), 'Invalid manual context binding identity.')
    return binding


def validate_envelope(value):
    require(isinstance(value, dict) and set(value) == ENVELOPE_FIELDS, 'Expected exactly the envelope v1 fields.')
    require(type(value['envelope_version']) is int and value['envelope_version'] == 1, 'Unsupported envelope_version; expected 1.')
    for key in ('project_root', 'task_ref'):
        path = value[key]
        if key == 'task_ref' and path is None:
            continue
        require(isinstance(path, str) and bool(path) and len(path) <= 8192 and Path(path).is_absolute()
                and os.path.normpath(path) == path, f'{key} must be an absolute canonical path.')
    require(isinstance(value['session_id'], str) and 0 < len(value['session_id']) <= 256
            and not any(ord(c) < 32 for c in value['session_id']), 'Explicit session identity is required (1–256 characters).')
    validate_binding(value['context_binding'])
    try:
        stamp = datetime.fromisoformat(value['exported_at'])
        require(stamp.tzinfo is not None, 'Export time requires a timezone.')
    except (TypeError, ValueError) as error:
        raise ValueError('exported_at must be an ISO timestamp with timezone.') from error
    result = validate_snapshot(value['snapshot'])
    require(result.valid, '; '.join(result.errors))
    require(value['snapshot']['scope'] == 'goal', 'File reports require complete goal scope.')
    require(len(canonical_bytes(value)) <= MAX_ENVELOPE_BYTES, 'Envelope exceeds 96 KiB.')
    return value


def parse_envelope(raw):
    return validate_envelope(strict_json(raw))


def history_key(envelope):
    return hashlib.sha256(canonical_bytes({key: envelope[key] for key in
        ('project_root', 'session_id', 'task_ref', 'context_binding')})).hexdigest()


@dataclass
class ChainResult:
    reports: list = field(default_factory=list)
    errors: list = field(default_factory=list)
    error_count: int = 0

    def error(self, message):
        self.error_count += 1
        if len(self.errors) < 100:
            self.errors.append(message[:512])


def classify_chain(reports, accepted=()):
    """Walk the sole unambiguous prefix; accepted bytes can never be displaced.

    Filename and export time do not choose a winner. A late ambiguity before an
    accepted head retains that decision and blocks new acceptance until resolved.
    """
    result = ChainResult(list(accepted))
    combined = list(reports) + list(accepted)
    if not combined:
        return result
    require(len({history_key(r) for r in combined}) == 1, 'Reports belong to different histories.')
    variants = {}
    for report in combined:
        variants.setdefault(report['snapshot']['snapshot_id'], {})[canonical_bytes(report)] = report
    conflicts = {key for key, values in variants.items() if len(values) > 1}
    for key in sorted(conflicts):
        result.error(f'Conflicting report ID: {key}. Previously accepted content is kept; conflicting reports are not applied.')
    by_id = {key: next(iter(values.values())) for key, values in variants.items()}
    by_id.update({r['snapshot']['snapshot_id']: r for r in accepted})
    children = {}
    roots = set()
    for key, values in variants.items():
        for report in values.values():
            parent = report['snapshot']['supersedes']
            if parent is None:
                roots.add(key)
            else:
                children.setdefault(parent, set()).add(key)
    if len(roots) != 1:
        result.error('History requires exactly one root; missing parent or multiple roots. No new reports were accepted.')
        return result
    prefix, visited = [], set()
    current = next(iter(roots))
    while current is not None:
        if current in visited:
            result.error('Snapshot ancestry cycle. No reports beyond the cycle were accepted.'); break
        if current in conflicts:
            break
        report = by_id[current]
        if prefix and report['snapshot']['goal_id'] != prefix[0]['snapshot']['goal_id']:
            result.error('Goal ID cannot change within a file history. The previous report is kept.'); break
        visited.add(current); prefix.append(report)
        following = children.get(current, set())
        if len(following) > 1:
            result.error('History has multiple children. The last report before the branch is kept.'); break
        current = next(iter(following)) if following else None
    if len(visited) != len(by_id):
        result.error('History has a missing parent, conflict or disconnected ancestry; later reports are not applied.')
    accepted_ids = [r['snapshot']['snapshot_id'] for r in accepted]
    prefix_ids = [r['snapshot']['snapshot_id'] for r in prefix]
    # A late conflicting variant of any accepted report cannot be traversed.
    if not (set(accepted_ids) & conflicts) and prefix_ids[:len(accepted_ids)] == accepted_ids:
        result.reports = prefix
    return result


def chain_head(reports):
    """Strict export wrapper around the collector's shared ancestry rules."""
    result = classify_chain(reports)
    require(not result.errors, '; '.join(result.errors))
    return result.reports[-1] if result.reports else None


@dataclass
class ScanResult:
    reports: list = field(default_factory=list)
    errors: list = field(default_factory=list)
    complete: bool = True
    count: int = 0
    byte_count: int = 0
    error_count: int = 0
    sources: dict = field(default_factory=dict)

    def error(self, message):
        self.error_count += 1
        if len(self.errors) < 100:
            self.errors.append(message[:512])


def snapshot_directory(project):
    directory = Path(project) / '.session-snapshots'
    require(not directory.is_symlink(), 'Snapshot directory must not be a symlink.')
    if directory.exists():
        require(directory.is_dir(), 'Snapshot destination is not a directory.')
    return directory


def _identity(info):
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def scan_reports(project, *, max_files=MAX_FILES, max_bytes=MAX_SCAN_BYTES):
    """Read stable regular .json candidates only; never read task documents.

    All candidate sizes/counts consume budget even when malformed. A concurrent
    publication or mutation makes this scan incomplete rather than a partial view.
    """
    result = ScanResult()
    directory = snapshot_directory(project)
    if not directory.exists():
        return result
    directory_fd = None
    try:
        directory_fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        before = os.fstat(directory_fd)
        candidates = []
        with os.scandir(directory_fd) as entries:
            for entry in entries:
                if not entry.name.endswith('.json') or entry.name.startswith('.tmp-'):
                    continue
                result.count += 1
                if result.count > max_files:
                    result.complete = False; result.error('Snapshot collection exceeds file budget.'); return result
                info = entry.stat(follow_symlinks=False)
                result.byte_count += info.st_size
                if result.byte_count > max_bytes:
                    result.complete = False; result.error('Snapshot collection exceeds byte budget.'); return result
                candidates.append((entry.name, info))
        for name, initial in sorted(candidates):
            if not stat.S_ISREG(initial.st_mode):
                result.error(f'{name}: report must be a regular file, not a symlink.'); continue
            if initial.st_size > MAX_ENVELOPE_BYTES:
                result.error(f'{name}: envelope exceeds 96 KiB.'); continue
            try:
                fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory_fd)
                with os.fdopen(fd, 'rb') as stream:
                    require(_identity(os.fstat(stream.fileno())) == _identity(initial), 'Report changed during scan.')
                    raw = stream.read(MAX_ENVELOPE_BYTES + 1)
                    require(_identity(os.fstat(stream.fileno())) == _identity(initial), 'Report changed during scan.')
                value = parse_envelope(raw)
                require(value['project_root'] == str(project), 'Envelope project does not match selected project.')
                result.reports.append(value)
                digest = hashlib.sha256(canonical_bytes(value)).hexdigest()
                result.sources.setdefault(digest, []).append(name)
            except ValueError as error:
                result.error(f'{name}: {error}')
                if str(error) == 'Report changed during scan.': result.complete = False
            except OSError:
                result.complete = False; result.error(f'{name}: report unreadable; collection incomplete.')
        # Recheck every candidate, including oversized/rejected entries.
        for name, initial in candidates:
            if _identity(os.stat(name, dir_fd=directory_fd, follow_symlinks=False)) != _identity(initial):
                result.complete = False; result.error('Report changed during scan.')
        if directory.is_symlink() or _identity(directory.stat()) != _identity(before) or _identity(os.fstat(directory_fd)) != _identity(before):
            result.complete = False; result.error('Snapshot directory changed during scan; Refresh again.')
    except OSError:
        result.complete = False; result.error('Snapshot directory is unreadable; collection incomplete.')
    finally:
        if directory_fd is not None:
            os.close(directory_fd)
    return result
