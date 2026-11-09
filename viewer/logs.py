"""Bounded read-only discovery and JSONL reads. No client identity inference."""
from dataclasses import dataclass, field
import hashlib
import json
import os
from pathlib import Path

MAX_LINE = 1024 * 1024
MAX_BATCH = 4 * MAX_LINE


def reject_constant(value):
    raise ValueError(f'Nonfinite JSON constant: {value}')


def parse_record(line):
    return json.loads(line, parse_constant=reject_constant)


@dataclass(frozen=True)
class RecordKey:
    file_identity: str
    generation: int
    offset: int

    def __str__(self):
        return f'{self.file_identity}:{self.generation}:{self.offset}'


@dataclass
class Cursor:
    offset: int = 0
    identity: str = ''
    generation: int = 0
    prefix: str = ''
    checkpoint: str = ''
    skipping: bool = False


@dataclass
class Recorded:
    key: RecordKey
    record: dict


@dataclass
class ReadBatch:
    records: list[Recorded]
    cursor: Cursor
    warnings: list[str] = field(default_factory=list)
    limited: bool = False


@dataclass
class SessionSummary:
    id: str
    path: Path
    session_id: str = ''
    project: str = ''
    recorded_version: str = ''
    recorded_source: object = None
    originator: str = ''
    status: str = 'unreadable'
    modified_at: float | None = None
    warning: str = ''

    def public(self):
        return {k: v for k, v in vars(self).items() if k != 'path'}


def discover_sessions(root: Path) -> list[SessionSummary]:
    root = root.resolve()
    result = []
    for candidate in sorted(root.rglob('*.jsonl')):
        summary = None
        try:
            path = candidate.resolve(strict=True)
            if not path.is_relative_to(root) or not path.is_file():
                continue
            opaque_id = hashlib.sha256(str(candidate.relative_to(root)).encode()).hexdigest()[:24]
            summary = SessionSummary(opaque_id, candidate)
            result.append(summary)
            summary.modified_at = path.stat().st_mtime
            with path.open('rb') as stream:
                line = stream.readline(MAX_LINE + 1)
            if len(line) > MAX_LINE or not line.endswith(b'\n'):
                raise ValueError('Metadata incomplete or exceeds 1 MiB.')
            record = parse_record(line)
            if not isinstance(record, dict) or record.get('type') != 'session_meta':
                raise ValueError('First record is not session metadata.')
            payload = record.get('payload')
            if not isinstance(payload, dict):
                raise ValueError('Invalid session metadata.')
            for attr, key in [('session_id', 'id'), ('project', 'cwd'), ('recorded_version', 'cli_version'), ('originator', 'originator')]:
                value = payload.get(key, '')
                setattr(summary, attr, value if isinstance(value, str) else '')
            summary.recorded_source = payload.get('source')
            summary.status = 'supported' if summary.recorded_version == '0.167.0' else 'unsupported'
            if summary.status == 'unsupported':
                summary.warning = 'Unsupported recorded version; evidence adapter is disabled.'
        except (OSError, ValueError, RecursionError):
            if summary is not None:
                summary.warning = 'Metadata unavailable, malformed or exceeds 1 MiB.'
    # Stable filename order breaks ties; unavailable timestamps belong last.
    result.sort(key=lambda session: (
        session.modified_at is None,
        -session.modified_at if session.modified_at is not None else 0,
    ))
    return result


def fingerprint(stream, offset):
    stream.seek(0)
    prefix = hashlib.sha256(stream.read(min(offset, 4096))).hexdigest()
    stream.seek(max(0, offset - 4096))
    checkpoint = hashlib.sha256(stream.read(min(offset, 4096))).hexdigest()
    return prefix, checkpoint


def read_delta(path: Path, cursor: Cursor | None) -> ReadBatch:
    with path.open('rb') as stream:
        stat = os.fstat(stream.fileno())
        identity = f'{stat.st_dev}-{stat.st_ino}'
        previous = cursor or Cursor(identity=identity)
        changed = previous.offset and previous.prefix and fingerprint(stream, previous.offset) != (previous.prefix, previous.checkpoint)
        reset = previous.identity != identity or stat.st_size < previous.offset or bool(changed)
        current = Cursor(0 if reset else previous.offset, identity, previous.generation + int(reset),
                         skipping=False if reset else previous.skipping)
        batch = ReadBatch([], current)
        if reset:
            batch.warnings.append('File replaced or truncated; new generation. Previous declarations may be stale.')
        stream.seek(current.offset)
        start = current.offset
        while stream.tell() - start < MAX_BATCH:
            position = stream.tell()
            line = stream.readline(min(MAX_LINE + 1, MAX_BATCH - (position - start)))
            if not line:
                break
            if current.skipping or len(line) > MAX_LINE:
                if not current.skipping:
                    batch.warnings.append(f'Record at byte {position} exceeds 1 MiB; omitted.')
                current.skipping = not line.endswith(b'\n')
                current.offset = stream.tell()
                batch.limited = True
                continue
            if not line.endswith(b'\n'):
                # A batch limit is not a record limit: retry from this line next poll.
                break
            current.offset = stream.tell()
            try:
                record = parse_record(line)
                if not isinstance(record, dict):
                    raise ValueError('Record must be an object')
                batch.records.append(Recorded(RecordKey(identity, current.generation, position), record))
            except (ValueError, RecursionError):
                batch.warnings.append(f'Malformed record at byte {position}; skipped.')
        if current.offset < stat.st_size and stream.tell() - start >= MAX_BATCH:
            batch.limited = True
            batch.warnings.append('Read limited to 4 MiB; collection continues next poll.')
        current.prefix, current.checkpoint = fingerprint(stream, current.offset)
        return batch
