"""Durable acceptance of immutable file reports, separate from boundary state.

Restart with this ledger preserves decisions and cached bytes. Removing it
intentionally rebuilds from the unambiguous prefix of the files still present.
Back up this database and the JSON sidecar together with the viewer stopped.
"""
import base64
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3

from .snapshot_files import (MAX_FILES, MAX_SCAN_BYTES, canonical_bytes, canonical_project,
                             classify_chain, history_key, parse_envelope, scan_reports, strict_json)
from .snapshots import require

PAGE_SIZE = 100
MAX_ERRORS = 100
MAX_SOURCE_NAMES = 100


def ledger_path(state_file):
    require(state_file is not None, 'A state file is required for durable report history.')
    path = Path(state_file).absolute()
    require(path.suffix != '.jsonl', 'State file cannot be a JSONL log.')
    return path.with_suffix('.snapshots.sqlite3')


class SnapshotLedger:
    def __init__(self, state_file, *, log_root=None):
        self.path = ledger_path(state_file)
        self.log_root = Path(log_root if log_root is not None else Path(os.environ.get('CODEX_HOME', Path.home() / '.codex')) / 'sessions').resolve()
        self._last_views = {}

    @contextmanager
    def _connection(self):
        require(self.path.resolve() == self.path and not self.path.is_symlink(),
                'Report history storage must not use symlink paths.')
        require(not (self.path.is_relative_to(self.log_root) or self.log_root.is_relative_to(self.path)),
                'Report history storage must not overlap read-only logs.')
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=5, isolation_level=None)
        connection.row_factory = sqlite3.Row
        try:
            connection.execute('PRAGMA foreign_keys = ON')
            connection.execute('PRAGMA synchronous = FULL')
            version = connection.execute('PRAGMA user_version').fetchone()[0]
            require(version in (0, 1), 'Unsupported report history storage version.')
            if version == 0:
                require(not connection.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall(),
                        'Report history storage has an unknown schema; repair it before collecting reports.')
                connection.executescript('''
                    BEGIN IMMEDIATE;
                    CREATE TABLE projects (project TEXT PRIMARY KEY, revision INTEGER NOT NULL);
                    CREATE TABLE histories (
                        project TEXT NOT NULL, history_id TEXT NOT NULL, head TEXT NOT NULL,
                        revision INTEGER NOT NULL, PRIMARY KEY(project, history_id));
                    CREATE TABLE reports (
                        project TEXT NOT NULL, history_id TEXT NOT NULL, report_id TEXT NOT NULL,
                        parent TEXT, sequence INTEGER NOT NULL, envelope BLOB NOT NULL,
                        digest TEXT NOT NULL, accepted_at TEXT NOT NULL,
                        source_names TEXT NOT NULL, source_count INTEGER NOT NULL, retained INTEGER NOT NULL,
                        PRIMARY KEY(project, history_id, report_id),
                        UNIQUE(project, history_id, sequence),
                        FOREIGN KEY(project, history_id) REFERENCES histories(project, history_id));
                    PRAGMA user_version = 1;
                    COMMIT;
                ''')
            yield connection
        finally:
            connection.close()

    def _rows(self, connection, project, history_id):
        rows = connection.execute('SELECT * FROM reports WHERE project=? AND history_id=? ORDER BY sequence',
                                  (project, history_id)).fetchall()
        previous = None
        goal_id = None
        for index, row in enumerate(rows, 1):
            raw = bytes(row['envelope'])
            require(hashlib.sha256(raw).hexdigest() == row['digest'], 'Accepted report hash does not match stored content.')
            envelope = parse_envelope(raw)
            snapshot = envelope['snapshot']
            require(envelope['project_root'] == project and history_key(envelope) == history_id
                    and snapshot['snapshot_id'] == row['report_id'] and row['sequence'] == index
                    and snapshot['supersedes'] == row['parent'] == previous,
                    'Accepted report history is inconsistent; repair storage before collecting reports.')
            require(goal_id is None or goal_id == snapshot['goal_id'], 'Accepted report goal is inconsistent.')
            require(canonical_bytes(envelope) == raw, 'Accepted report bytes are not canonical.')
            previous, goal_id = row['report_id'], snapshot['goal_id']
        return rows

    def _entry(self, row):
        raw = bytes(row['envelope'])
        require(hashlib.sha256(raw).hexdigest() == row['digest'], 'Accepted report hash does not match stored content.')
        envelope = parse_envelope(raw)
        require(canonical_bytes(envelope) == raw and envelope['project_root'] == row['project']
                and history_key(envelope) == row['history_id']
                and envelope['snapshot']['snapshot_id'] == row['report_id']
                and envelope['snapshot']['supersedes'] == row['parent'], 'Accepted report content is inconsistent.')
        sources = json.loads(row['source_names'])
        require(isinstance(sources, list) and len(sources) <= MAX_SOURCE_NAMES
                and all(isinstance(name, str) and len(name) <= 255 for name in sources),
                'Accepted report provenance is invalid.')
        retained = bool(row['retained'])
        return dict(envelope, accepted_at=row['accepted_at'], content_hash=row['digest'], sequence=row['sequence'],
                    source_names=sources, source_count=row['source_count'], retained=retained,
                    status='Retained history: original file is missing or changed.' if retained else 'Saved report')

    def _history_view(self, connection, project, history):
        count = connection.execute('SELECT COUNT(*) FROM reports WHERE project=? AND history_id=?',
                                   (project, history['history_id'])).fetchone()[0]
        row = connection.execute('SELECT * FROM reports WHERE project=? AND history_id=? AND report_id=?',
                                 (project, history['history_id'], history['head'])).fetchone()
        require(row is not None and row['sequence'] == count, 'Accepted report head is inconsistent.')
        current = self._entry(row)
        return dict(history_id=history['history_id'], revision=history['revision'], count=count,
                    project_root=project, session_id=current['session_id'], task_ref=current['task_ref'],
                    context_binding=current['context_binding'], current=current)

    def _histories(self, connection, project):
        return [self._history_view(connection, project, history) for history in
                connection.execute('SELECT * FROM histories WHERE project=? ORDER BY history_id', (project,))]

    def histories(self, project):
        """Read committed heads only; this never scans the report directory."""
        project = str(canonical_project(project))
        try:
            with self._connection() as connection:
                connection.execute('BEGIN')
                result = self._histories(connection, project)
                connection.commit()
                return result
        except (sqlite3.Error, OSError, ValueError) as error:
            raise ValueError('Report history storage is unavailable or corrupt; no new reports were accepted. Repair storage before collecting reports.') from error

    def collect(self, project, *, max_files=MAX_FILES, max_bytes=MAX_SCAN_BYTES):
        """One explicit stable scan; all accepted heads publish after one commit."""
        project = str(canonical_project(project))
        errors, error_count = [], 0
        def diagnostic(message):
            nonlocal error_count
            error_count += 1
            if len(errors) < MAX_ERRORS:
                errors.append(message[:512])
        try:
            directory = Path(project) / '.session-snapshots'
            require(not (self.path.is_relative_to(directory) or directory.is_relative_to(self.path)),
                    'Report history storage must not overlap report files.')
            with self._connection() as connection:
                connection.execute('BEGIN IMMEDIATE')
                previous = self._histories(connection, project)
                old_revision = connection.execute('SELECT revision FROM projects WHERE project=?', (project,)).fetchone()
                revision = old_revision[0] if old_revision else 0
                self._last_views[project] = (revision, previous)
                scan = scan_reports(Path(project), max_files=max_files, max_bytes=max_bytes)
                errors.extend(scan.errors[:MAX_ERRORS]); error_count += scan.error_count
                if not scan.complete:
                    connection.rollback()
                    return dict(complete=False, revision=revision, errors=errors, error_count=error_count, histories=previous)
                grouped = {}
                for report in scan.reports:
                    grouped.setdefault(history_key(report), []).append(report)
                existing = {item['history_id']: item for item in previous}
                any_change = False
                now = datetime.now(timezone.utc).isoformat()
                for key in sorted(set(grouped) | set(existing)):
                    rows = self._rows(connection, project, key)
                    accepted = [parse_envelope(bytes(row['envelope'])) for row in rows]
                    chain = classify_chain(grouped.get(key, []), accepted)
                    for message in chain.errors:
                        if len(errors) < MAX_ERRORS: errors.append(message[:512])
                    error_count += chain.error_count
                    additions = chain.reports[len(accepted):]
                    if not rows and not additions:
                        continue
                    changed = bool(additions)
                    if not rows:
                        connection.execute('INSERT INTO histories VALUES(?,?,?,0)',
                                           (project, key, additions[-1]['snapshot']['snapshot_id']))
                    for row in rows:
                        names = sorted(scan.sources.get(row['digest'], []))
                        retained = int(not names)
                        # Preserve original provenance when files disappear/change.
                        sources = json.dumps(names[:MAX_SOURCE_NAMES]) if names else row['source_names']
                        count = len(names) if names else row['source_count']
                        if (sources, count, retained) != (row['source_names'], row['source_count'], row['retained']):
                            changed = True
                            connection.execute('UPDATE reports SET source_names=?,source_count=?,retained=? WHERE project=? AND history_id=? AND report_id=?',
                                               (sources, count, retained, project, key, row['report_id']))
                        if retained:
                            diagnostic(f"{row['report_id']}: Retained history: original file is missing or changed.")
                    for sequence, report in enumerate(additions, len(rows) + 1):
                        raw = canonical_bytes(report); digest = hashlib.sha256(raw).hexdigest()
                        names = sorted(scan.sources.get(digest, []))
                        connection.execute('INSERT INTO reports VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                            (project, key, report['snapshot']['snapshot_id'], report['snapshot']['supersedes'], sequence,
                             raw, digest, now, json.dumps(names[:MAX_SOURCE_NAMES]), len(names), 0))
                    if changed:
                        any_change = True
                        head = chain.reports[-1]['snapshot']['snapshot_id']
                        connection.execute('UPDATE histories SET head=?,revision=revision+1 WHERE project=? AND history_id=?', (head, project, key))
                if any_change:
                    revision += 1
                    connection.execute('INSERT INTO projects VALUES(?,?) ON CONFLICT(project) DO UPDATE SET revision=excluded.revision', (project, revision))
                current = self._histories(connection, project)
                connection.commit()
                self._last_views[project] = (revision, current)
                return dict(complete=True, revision=revision, errors=errors, error_count=error_count, histories=current)
        except (sqlite3.Error, OSError, ValueError) as error:
            diagnostic('Report history storage is unavailable or corrupt; no new reports were accepted. Repair storage before collecting reports.')
            revision, previous = self._last_views.get(project, (0, []))
            return dict(complete=False, revision=revision, errors=errors, error_count=error_count, histories=previous)

    def lookup(self, project, history_id, report_ids):
        """Resolve a bounded legacy stream's IDs without scanning or paging history."""
        require(isinstance(report_ids, list) and len(report_ids) <= 128
                and all(isinstance(item, str) and len(item) <= 128 for item in report_ids),
                'Report lookup requires at most 128 bounded IDs.')
        project = str(canonical_project(project))
        if not report_ids: return []
        try:
            with self._connection() as connection:
                placeholders = ','.join('?' for _ in report_ids)
                rows = connection.execute(f'SELECT * FROM reports WHERE project=? AND history_id=? AND report_id IN ({placeholders})',
                    (project, history_id, *report_ids)).fetchall()
                return [self._entry(row) for row in rows]
        except (sqlite3.Error, OSError) as error:
            raise ValueError('Report history storage is unavailable or corrupt; repair storage before reading history.') from error

    def page(self, project, history_id, *, cursor=None, limit=PAGE_SIZE):
        """Newest-first accepted history, with a cursor bound to history revision."""
        require(type(limit) is int and 1 <= limit <= PAGE_SIZE, 'History page size must be between 1 and 100.')
        project = str(canonical_project(project))
        try:
            with self._connection() as connection:
                connection.execute('BEGIN')
                saved = connection.execute('SELECT * FROM histories WHERE project=? AND history_id=?', (project, history_id)).fetchone()
                require(saved is not None, 'Unknown report history.')
                history = self._history_view(connection, project, saved)
                offset = 0
                if cursor is not None:
                    require(isinstance(cursor, str) and len(cursor) <= 1024, 'Invalid history cursor.')
                    try:
                        value = strict_json(base64.b64decode(cursor.encode('ascii'), altchars=b'-_', validate=True), 1024)
                    except (ValueError, UnicodeError) as error:
                        raise ValueError('Invalid history cursor.') from error
                    require(isinstance(value, dict) and set(value) == {'history_id','project','revision','offset'}
                            and value['history_id'] == history_id and value['project'] == project,
                            'History cursor belongs to another project or history.')
                    require(value['revision'] == history['revision'], 'Report history changed; start paging again.')
                    require(type(value['offset']) is int and 0 <= value['offset'] < history['count'], 'Invalid history cursor position.')
                    offset = value['offset']
                rows = connection.execute('SELECT * FROM reports WHERE project=? AND history_id=? ORDER BY sequence DESC LIMIT ? OFFSET ?',
                                          (project, history_id, limit, offset)).fetchall()
                entries = [self._entry(row) for row in rows]
                following = offset + len(entries)
                next_cursor = None
                if following < history['count']:
                    next_cursor = base64.urlsafe_b64encode(canonical_bytes(dict(project=project, history_id=history_id,
                        revision=history['revision'], offset=following))).decode('ascii')
                connection.commit()
                return dict(history_id=history_id, revision=history['revision'], current=history['current'],
                            entries=entries, next_cursor=next_cursor, count=history['count'])
        except (sqlite3.Error, OSError) as error:
            raise ValueError('Report history storage is unavailable or corrupt; repair storage before reading history.') from error
