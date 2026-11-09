"""Versioned, atomic sidecar storage; never writes into a log file."""
import json
import os
from pathlib import Path
import tempfile


class State:
    def __init__(self, path):
        self.path = Path(path) if path else None
        self.data = {'version': 1, 'sessions': {}}
        self.warning = ''
        if self.path and self.path.exists():
            try:
                if self.path.stat().st_size > 4 * 1024 * 1024:
                    raise ValueError('State too large')
                data = json.loads(self.path.read_text())
                if (not isinstance(data, dict) or data.get('version') != 1
                        or not isinstance(data.get('sessions'), dict)):
                    raise ValueError('Unknown state schema')
                for item in data['sessions'].values():
                    if (not isinstance(item, dict) or not isinstance(item.get('cursor'), dict)
                            or not isinstance(item.get('boundaries'), list)):
                        raise ValueError('Invalid session state')
                    cursor = item['cursor']
                    if set(cursor) != {'offset', 'identity', 'generation', 'prefix', 'checkpoint', 'skipping'} or not isinstance(cursor['skipping'], bool):
                        raise ValueError('Invalid cursor fields')
                    if (not isinstance(cursor.get('offset'), int) or not 0 <= cursor['offset'] < 2**63
                            or not isinstance(cursor.get('generation'), int) or cursor['generation'] < 0
                            or any(not isinstance(cursor.get(k), str) for k in ('identity', 'prefix', 'checkpoint'))):
                        raise ValueError('Invalid cursor')
                    for b in item['boundaries']:
                        if (not isinstance(b, dict) or not isinstance(b.get('offset'), int) or not 0 < b['offset'] < 2**63
                                or any(not isinstance(b.get(k), str) for k in ('id', 'generation', 'origin', 'created_at'))):
                            raise ValueError('Invalid boundary')
                flags = data.get('flags', [])
                if not isinstance(flags, list) or len(flags) > 2048:
                    raise ValueError('Invalid flags')
                for flag in flags:
                    required = {'id', 'author_type', 'context_key', 'generation', 'source_id', 'evidence_key', 'reason', 'created_at'}
                    if (not isinstance(flag, dict) or set(flag) != required or flag['author_type'] != 'user'
                            or any(not isinstance(flag[k], str) or not flag[k] for k in ('id', 'context_key', 'generation', 'reason', 'created_at'))
                            or len(flag['reason']) > 8192
                            or any(flag[k] is not None and not isinstance(flag[k], str) for k in ('source_id', 'evidence_key'))):
                        raise ValueError('Invalid flag')
                self.data = data
            except (OSError, ValueError, RecursionError):
                self.warning = 'Sidecar state is unreadable or corrupt; declarations disabled until repaired.'

    def save(self, data):
        if self.path and self.path.suffix == '.jsonl':
            raise ValueError('State file cannot be a JSONL log.')
        if self.warning:
            raise ValueError(self.warning)
        if self.path is None:
            raise ValueError('No state file configured; boundary persistence unavailable.')
        temporary = None
        try:
            encoded = json.dumps(data, ensure_ascii=False).encode()
            if len(encoded) > 4 * 1024 * 1024:
                raise ValueError('Sidecar state exceeds 4 MiB.')
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(dir=self.path.parent, delete=False) as stream:
                temporary = stream.name
                stream.write(encoded)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
            self.data = data
        except OSError as error:
            raise ValueError('Sidecar state write failed; declaration was not changed.') from error
        finally:
            if temporary and os.path.exists(temporary):
                os.unlink(temporary)
