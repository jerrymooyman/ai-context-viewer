"""Incremental selected-log cache and explicit generation-scoped projections."""
from copy import deepcopy
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
from pathlib import Path
import uuid

from .codex_0167 import normalize
from .logs import Cursor, SessionSummary, read_delta
from .snapshots import SnapshotHistory, ValidationResult, apply_snapshot, parse_snapshot, extract_snapshots
from .state import State
from .sources import project_sources

COVERAGE = ('Recorded exposure only; full prompt coverage, current residency and causal influence are unknown. '
            'Same-session resets require an explicit user boundary; activity alone never implies reset. File revision checks sample consumed prefix/checkpoint bytes; arbitrary middle rewrites can be missed.')
MAX_RECORDS = 5000
MAX_CACHE_BYTES = 8 * 1024 * 1024
PAGE_SIZE = 100


def project_contexts(records, boundaries):
    """Assign solely by explicit byte boundaries; carry no records forward."""
    starts = [0] + [b['offset'] for b in sorted(boundaries, key=lambda b: b['offset'])]
    return [[r for r in records if start <= r.key.offset < (starts[i + 1] if i + 1 < len(starts) else float('inf'))]
            for i, start in enumerate(starts)]


class ContextStore:
    def __init__(self, root, state_file=None):
        self.root = root.resolve()
        self.state = State(state_file)
        self.cache = {}
        self.import_contexts = {}
        self.manual_events = {}
        self.snapshot_bytes = 0

    def load(self, summary):
        path = summary.path.resolve(strict=True)
        if not path.is_relative_to(self.root):
            raise OSError('Outside log root')
        entry = self.cache.get(summary.id)
        if entry is None:
            saved = self.state.data['sessions'].get(summary.id, {})
            saved_cursor = saved.get('cursor')
            if saved_cursor:
                # Validate the saved generation before replaying its evidence after restart.
                checked = read_delta(path, Cursor(**saved_cursor)).cursor
                cursor = Cursor(identity=checked.identity, generation=checked.generation)
            else:
                cursor = None
            entry = {'cursor': cursor, 'records': [], 'warnings': [], 'bytes': 0, 'evicted': False, 'snapshot_events': [], 'snapshot_keys': set()}
            self.cache[summary.id] = entry
        if entry.pop('replay', False):
            checked = read_delta(path, entry['cursor']).cursor
            entry['cursor'] = Cursor(identity=checked.identity, generation=checked.generation)
        # Keep bodies for only the selected log. Other entries retain small cursors.
        for other_id, other in self.cache.items():
            if other_id != summary.id and other['records']:
                other.update(records=[], bytes=0, replay=True)
        batch = read_delta(path, entry['cursor'])
        entry['collection_status'] = 'following' if batch.records else 'idle'
        previous = entry['cursor']
        if previous and (previous.identity, previous.generation) != (batch.cursor.identity, batch.cursor.generation):
            self.snapshot_bytes -= sum(len(e['raw'].encode()) for e in entry['snapshot_events'])
            entry.update(records=[], warnings=[], bytes=0, evicted=False, snapshot_events=[], snapshot_keys=set())
        entry['cursor'] = batch.cursor
        for recorded in batch.records:
            key = str(recorded.key)
            blocks = extract_snapshots(recorded)
            if blocks and key not in entry['snapshot_keys']:
                entry['snapshot_keys'].add(key)
                for raw in blocks:
                    if self.snapshot_bytes + len(raw.encode()) > MAX_CACHE_BYTES or len(entry['snapshot_events']) >= 256:
                        entry['warnings'].append('Snapshot collection capacity reached; later snapshots omitted, last valid retained.')
                        break
                    self.event_order = getattr(self, 'event_order', 0) + 1
                    entry['snapshot_events'].append(dict(order=self.event_order, raw=raw, offset=recorded.key.offset, evidence_key=key,
                        origin='agent-self-report', observed_at=recorded.record.get('timestamp'), context_id=None))
                    self.snapshot_bytes += len(raw.encode())
        entry['records'].extend(batch.records)
        entry['bytes'] += sum(len(str(r.record).encode()) for r in batch.records)
        while len(entry['records']) > MAX_RECORDS or entry['bytes'] > MAX_CACHE_BYTES:
            entry['bytes'] -= len(str(entry['records'].pop(0).record).encode())
            entry['evicted'] = True
        entry['warnings'] = list(dict.fromkeys(entry['warnings'] + batch.warnings))[-100:]
        return entry

    def generation(self, entry):
        c = entry['cursor']
        return f'{c.identity}:{c.generation}'

    def boundaries(self, summary, entry=None):
        entry = entry or self.cache.get(summary.id)
        saved = self.state.data['sessions'].get(summary.id, {})
        boundaries = saved.get('boundaries', [])
        return [b for b in boundaries if not entry or b['generation'] == self.generation(entry)]

    def list_contexts(self, summary):
        if summary.id in self.import_contexts:
            return [dict(id=summary.id, label='User-imported context (temporary)', boundary=None)]
        return [dict(id=summary.id, label='Initial context', boundary=None)] + [
            dict(id=b['id'], label=f"Declared context at byte {b['offset']}", boundary=b)
            for b in sorted(self.boundaries(summary), key=lambda b: b['offset'])]

    def view(self, summary, context_id, after=-1, limit=PAGE_SIZE):
        result = dict(id=context_id, session=summary.public(), status=summary.status,
                      supplied=[], activity=[], revision=None, activity_at=None, snapshot_at=None, sources=[], flags=[],
                      warnings=[COVERAGE], next=None, boundary=None)
        if self.state.warning:
            result['warnings'].append(self.state.warning)
        if summary.warning:
            result['warnings'].append(summary.warning)
        if summary.status != 'supported':
            self.snapshot_view(summary, context_id, result)
            self.source_view(result, [])
            return result
        entry = self.load(summary)
        boundaries = sorted(self.boundaries(summary, entry), key=lambda b: b['offset'])
        ids = [summary.id] + [b['id'] for b in boundaries]
        if context_id not in ids:
            raise ValueError('Context boundary is stale or unavailable.')
        index = ids.index(context_id)
        if index:
            result['boundary'] = boundaries[index - 1]
        c = entry['cursor']
        result['collection_status'] = entry['collection_status']
        result['generation'] = self.generation(entry)
        result['revision'] = f'{self.generation(entry)}:{c.offset}'
        records = project_contexts(entry['records'], boundaries)[index]
        for r in reversed(records):
            if isinstance(r.record.get('timestamp'), str):
                result['activity_at'] = r.record['timestamp']
                break
        page = [r for r in records if r.key.offset > after][:limit + 1]
        if len(page) > limit:
            result['next'] = page[limit - 1].key.offset
        for r in page[:limit]:
            for evidence in normalize(r.record, r.key):
                result['supplied' if evidence.kind == 'supplied' else 'activity'].append(asdict(evidence))
        result['warnings'].extend(entry['warnings'])
        if entry['evicted']:
            result['warnings'].append('Earlier evidence omitted: selected-log cache is bounded to 5000 records / 8 MiB.')
        all_boundaries = self.state.data['sessions'].get(summary.id, {}).get('boundaries', [])
        if len(all_boundaries) != len(boundaries):
            result['warnings'].append('Stale declarations from a previous file generation were not applied.')
        self.snapshot_view(summary, context_id, result, entry, boundaries)
        evidence = [asdict(e) for r in records for e in normalize(r.record, r.key)]
        self.source_view(result, evidence)
        result['context_target'] = dict(session_id=summary.session_id, context_binding=(dict(version=1, kind='manual', context_id=context_id, generation=result['generation'], boundary_record_key=f"{result['generation']}:{result['boundary']['offset']}") if result['boundary'] else dict(version=1, kind='initial')))
        return result

    def persist(self, summary, entry, boundaries):
        data = deepcopy(self.state.data)
        data['sessions'][summary.id] = dict(cursor=asdict(entry['cursor']), boundaries=boundaries)
        self.state.save(data)

    def declare(self, summary, context_id, record_key):
        self.view(summary, context_id)
        entry = self.cache[summary.id]
        target = next((r for r in entry['records'] if str(r.key) == record_key), None)
        if target is None or target.key.offset == 0:
            raise ValueError('Choose an available non-metadata record.')
        boundaries = deepcopy(self.state.data['sessions'].get(summary.id, {}).get('boundaries', []))
        generation = self.generation(entry)
        if any(b['offset'] == target.key.offset and b['generation'] == generation for b in boundaries):
            raise ValueError('A boundary already exists at that record.')
        context_list = self.list_contexts(summary)
        current = next(c for c in context_list if c['id'] == context_id)
        start = current['boundary']['offset'] if current['boundary'] else 0
        end = min((c['boundary']['offset'] for c in context_list if c['boundary'] and c['boundary']['offset'] > start), default=float('inf'))
        if not start < target.key.offset < end:
            raise ValueError('Record does not belong to the selected context interior.')
        boundary_id = hashlib.sha256(f'{summary.id}:{record_key}'.encode()).hexdigest()[:24]
        boundaries.append(dict(id=boundary_id, offset=target.key.offset, generation=generation,
                               origin='user-declared', created_at=datetime.now(timezone.utc).isoformat()))
        self.persist(summary, entry, boundaries)
        return boundary_id

    def remove(self, summary, context_id):
        self.view(summary, context_id)
        if context_id == summary.id:
            raise ValueError('Initial context cannot be removed.')
        entry = self.cache[summary.id]
        boundaries = self.state.data['sessions'].get(summary.id, {}).get('boundaries', [])
        self.persist(summary, entry, [b for b in boundaries if b['id'] != context_id])


    def create_import_context(self):
        if len(self.import_contexts) >= 64:
            raise ValueError('Temporary import context capacity reached (64).')
        context_id = uuid.uuid4().hex[:24]
        summary = SessionSummary(context_id, Path(), session_id=context_id, project='Manual snapshot import',
                                 recorded_version='', originator='user-imported', status='user-imported')
        self.import_contexts[context_id] = summary
        return summary

    def snapshot_view(self, summary, context_id, result, entry=None, boundaries=None):
        generation = self.generation(entry) if entry else 'unavailable-log'
        scope_key = f'{context_id}:{generation}'
        start, end = 0, float('inf')
        if entry:
            boundaries = boundaries if boundaries is not None else sorted(self.boundaries(summary, entry), key=lambda b: b['offset'])
            starts = [(summary.id, 0)] + [(b['id'], b['offset']) for b in boundaries]
            index = next(i for i, pair in enumerate(starts) if pair[0] == context_id)
            start = starts[index][1]
            if index + 1 < len(starts):
                end = starts[index + 1][1]
        events = [e for e in (entry['snapshot_events'] if entry else []) if start <= e['offset'] < end]
        events += self.manual_events.get(scope_key, [])
        events.sort(key=lambda e: e.get('order', 0))
        history, errors = SnapshotHistory(), []
        for event in events:
            try:
                payload = parse_snapshot(event['raw'])
                observed = event['observed_at'] if isinstance(event['observed_at'], str) else None
                valid = apply_snapshot(history, payload, scope_key, observed_at=observed,
                                       origin=event['origin'], evidence_key=event['evidence_key'])
                if not valid.valid:
                    raise ValueError('; '.join(valid.errors))
            except (ValueError, RecursionError) as error:
                errors.append(dict(error=str(error), origin=event['origin'], observed_at=event['observed_at'],
                                   evidence_key=event['evidence_key'], raw=event['raw']))
        result.update(snapshot=history.current, snapshot_history=history.history, snapshot_errors=errors,
                      snapshot_heads=history.heads, snapshot_at=history.history[-1]['observed_at'] if history.history else None)
        result['snapshot_revision'] = hashlib.sha256(str([(e['raw'], e['observed_at'], e['origin']) for e in events]).encode()).hexdigest()[:24]
        result['warnings'].append('Snapshots are agent self-report or user imports; source links show recorded evidence only; verification remains agent-reported. Imports are temporary and lost on service restart.')
        if not entry:
            result['warnings'].append('Log provenance and coverage are unknown for imported snapshots.')
        return history, errors, scope_key

    def import_snapshot(self, summary, context_id, raw, expected_generation=None):
        view = self.view(summary, context_id)
        if expected_generation is not None and expected_generation != view.get('generation', 'unavailable-log'):
            return ValidationResult(False, ['Context generation changed; inspect the context before importing.'])
        entry = self.cache.get(summary.id) if summary.status == 'supported' else None
        scope_key = f"{context_id}:{self.generation(entry) if entry else 'unavailable-log'}"
        events = self.manual_events.setdefault(scope_key, [])
        if len(events) >= 128 or self.snapshot_bytes + len(raw.encode()) > MAX_CACHE_BYTES:
            return ValidationResult(False, ['Temporary snapshot capacity reached; last valid snapshot retained.'])
        # Retain invalid attempts too, so the user can inspect the failure beside the last valid snapshot.
        if len(raw.encode()) > 65536:
            return ValidationResult(False, ['Snapshot exceeds 64 KiB.'])
        event = dict(raw=raw, observed_at=datetime.now(timezone.utc).isoformat(), origin='user-imported',
                     evidence_key=None, offset=None, context_id=context_id)
        self.event_order = getattr(self, 'event_order', 0) + 1
        event['order'] = self.event_order
        events.append(event)
        self.snapshot_bytes += len(raw.encode())
        result = self.view(summary, context_id)
        error = next((e for e in result['snapshot_errors'] if e['observed_at'] == event['observed_at'] and e['origin'] == 'user-imported'), None)
        return ValidationResult(not error, [error['error']] if error else [])

    def source_view(self, result, evidence):
        result['sources'] = project_sources(result.get('snapshot'), evidence)
        generation = result.get('generation', 'unavailable-log')
        result['flags'] = [f for f in self.state.data.get('flags', [])
                           if f['context_key'] == result['id'] and f['generation'] == generation]
        result['source_revision'] = hashlib.sha256(str((result['sources'], result['flags'])).encode()).hexdigest()[:24]

    def flag(self, summary, context_id, body):
        view = self.view(summary, context_id)
        return self.save_flag(view, body, summary)

    def save_flag(self, view, body, summary=None):
        context_id = view['id']
        if not isinstance(body, dict) or set(body) != {'generation', 'source_id', 'evidence_key', 'reason'}:
            raise ValueError('Supply generation, source_id, evidence_key and reason.')
        generation = view.get('generation', 'unavailable-log')
        if body['generation'] != generation:
            raise ValueError('Context generation changed; inspect the source again.')
        source_id, evidence_key, reason = body['source_id'], body['evidence_key'], body['reason']
        if ((source_id is not None and not isinstance(source_id, str))
                or (evidence_key is not None and not isinstance(evidence_key, str))
                or not isinstance(reason, str) or not reason.strip() or len(reason) > 8192):
            raise ValueError('Invalid flag target or reason (1–8192 characters).')
        if not any((s['id'] == source_id and (source_id is not None or evidence_key is not None)
                    and (evidence_key is None or evidence_key in s['evidence_keys'])) for s in view['sources']):
            raise ValueError('Source/evidence target is unavailable in this context.')
        data = deepcopy(self.state.data)
        flags = data.setdefault('flags', [])
        if len(flags) >= 2048:
            raise ValueError('Flag capacity reached (2048); no flag changed.')
        flag = dict(id=uuid.uuid4().hex, author_type='user', context_key=context_id, generation=generation,
                    source_id=source_id, evidence_key=evidence_key, reason=reason.strip(),
                    created_at=datetime.now(timezone.utc).isoformat())
        flags.append(flag)
        if summary is not None and summary.status == 'supported':
            entry = self.cache[summary.id]
            data['sessions'][summary.id] = dict(cursor=asdict(entry['cursor']),
                boundaries=deepcopy(data['sessions'].get(summary.id, {}).get('boundaries', [])))
        self.state.save(data)
        return flag


class FileContexts:
    """File understanding has its own chain; evidence is an optional attachment."""
    def __init__(self, project, store, state_file):
        from .snapshot_ledger import SnapshotLedger
        self.project = Path(project).resolve()
        self.store = store
        self.ledger = SnapshotLedger(state_file, log_root=store.root)
        self.collection = dict(complete=True, revision=0, errors=[], error_count=0)
        self.last_summaries = []
        self.last_view = None

    def collect(self):
        result = self.ledger.collect(self.project)
        self.collection = {k:v for k,v in result.items() if k != 'histories'}
        return self.collection

    def summaries(self):
        # Listing heads never scans exports and never returns every report body.
        try: histories = self.ledger.histories(self.project)
        except ValueError:
            if self.last_summaries: return self.last_summaries
            raise
        self.last_summaries = [{k:v for k,v in h.items() if k != 'current'} | dict(
                    current_report_id=h['current']['snapshot']['snapshot_id'],
                    exported_at=h['current']['exported_at'], retained=h['current']['retained'])
                for h in histories]
        return self.last_summaries

    def attachment(self, history, summaries, after=-1):
        matches = [s for s in summaries if s.session_id == history['session_id']]
        if len(matches) > 1:
            return None, None, 'ambiguous', 'Multiple recorded logs claim this identity; evidence is not attached.'
        if not matches:
            return None, None, 'unresolved', 'No matching recorded session; source coverage is unknown.'
        summary = matches[0]
        if not summary.project or not Path(summary.project).is_absolute() or Path(summary.project).resolve() != self.project:
            return None, None, 'unresolved', 'Recorded directory does not match this project; no evidence is attached.'
        if summary.status != 'supported':
            return summary, None, 'metadata-only', 'Unsupported recorded version; evidence coverage is unknown.'
        binding = history['context_binding']
        context_id = summary.id if binding['kind'] == 'initial' else binding['context_id']
        try:
            view = self.store.view(summary, context_id, after=after)
            if binding['kind'] == 'manual':
                boundary = view.get('boundary')
                if (not boundary or binding['generation'] != view.get('generation') or
                        binding['boundary_record_key'] != f"{view.get('generation')}:{boundary['offset']}"):
                    raise ValueError('Stale boundary')
            return summary, view, 'attached', 'Uniquely matched recorded context; coverage remains incomplete.'
        except (OSError, ValueError):
            return summary, None, 'unresolved', 'Context binding is stale or unavailable; no evidence is attached.'

    def view(self, history_id, summaries, *, cursor=None, limit=100, after=-1):
        try:
            result = self._view(history_id, summaries, cursor=cursor, limit=limit, after=after)
        except ValueError as error:
            # Retain the selected accepted understanding when storage fails.
            # Invalid/stale cursors must still fail instead of changing pages.
            storage_error = 'storage' in str(error).lower() or str(error).startswith('Accepted report')
            if not storage_error or not self.last_view or self.last_view['id'] != history_id:
                raise
            result = deepcopy(self.last_view)
            result.update(attachment='unresolved', supplied=[], activity=[], generation='unavailable-log',
                evidence_context_id=None, context_target=None, next=None)
            result['warnings'].append(str(error))
            self.store.source_view(result, [])
            return result
        self.last_view = deepcopy(result)
        return result

    def _view(self, history_id, summaries, *, cursor=None, limit=100, after=-1):
        from .snapshot_files import canonical_bytes
        page = self.ledger.page(self.project, history_id, cursor=cursor, limit=limit)
        current = page['current']
        summary, attached, status, message = self.attachment(current, summaries, after=after)
        result = dict(id=history_id, session=summary.public() if summary else dict(
            session_id=current['session_id'], project=str(self.project), recorded_version='', status='unresolved'),
            status='file', stream='files', attachment=status, supplied=[], activity=[], sources=[], flags=[],
            activity_at=None, generation='unavailable-log', revision=None, boundary=None, next=None,
            warnings=[COVERAGE, message, 'Saved files are agent self-report; export time and acceptance time do not verify understanding.'],
            evidence_context_id=summary.id if status == 'metadata-only' else None, context_target=None)
        if attached:
            context_id = attached['id']
            # Evidence paging is independent of report-history paging.
            evidence_view = attached
            for key in ('supplied','activity','activity_at','generation','revision','boundary','next','collection_status','context_target'):
                result[key] = evidence_view.get(key)
            result['evidence_context_id'] = context_id
            result['warnings'].extend(evidence_view['warnings'])
        snapshot = deepcopy(current['snapshot'])
        def entry(report):
            return dict(snapshot=report['snapshot'], observed_at=report['exported_at'], origin='file agent self-report',
                        evidence_key=None, accepted_at=report['accepted_at'], content_hash=report['content_hash'],
                        source_names=report['source_names'], source_count=report['source_count'],
                        retained=report['retained'], status=report['status'])
        current_entry = entry(current)
        result.update(snapshot=snapshot, snapshot_history=[entry(r) for r in page['entries']],
            snapshot_heads={scope: snapshot['snapshot_id'] for scope in ['goal'] + [t['id'] for t in snapshot['subtasks']]},
            current_report=current_entry, snapshot_at=current['exported_at'],
            snapshot_revision=f"{history_id}:{page['revision']}", snapshot_errors=[],
            history_next=page['next_cursor'], history_count=page['count'], history_revision=page['revision'],
            task_ref=current['task_ref'], context_binding=current['context_binding'], cross_stream=[])
        if current['retained']: result['warnings'].append(current['status'])
        result['warnings'].extend(self.collection['errors'])
        if attached:
            # Use all bounded records in the uniquely matched context, never a
            # different stream's snapshot-derived Sources projection.
            cache = self.store.cache[summary.id]
            boundaries = sorted(self.store.boundaries(summary, cache), key=lambda b:b['offset'])
            ids = [summary.id] + [b['id'] for b in boundaries]
            records = project_contexts(cache['records'], boundaries)[ids.index(attached['id'])]
            evidence = [asdict(e) for r in records for e in normalize(r.record, r.key)]
            ids_to_match = [old['snapshot']['snapshot_id'] for old in attached['snapshot_history']]
            file_reports = {r['snapshot']['snapshot_id']: r['snapshot'] for r in self.ledger.lookup(self.project, history_id, ids_to_match)}
            for old in attached['snapshot_history']:
                report_id = old['snapshot']['snapshot_id']
                if report_id in file_reports:
                    equal = canonical_bytes(old['snapshot']) == canonical_bytes(file_reports[report_id])
                    result['cross_stream'].append(dict(report_id=report_id,
                        status='linked' if equal else 'conflict', origin=old['origin'], evidence_key=old['evidence_key']))
            if any(item['status']=='conflict' for item in result['cross_stream']):
                result['warnings'].append('The same report ID has different content in log/import understanding. The streams remain separate.')
        else:
            evidence = []
        self.store.source_view(result, evidence)
        result['warnings'] = list(dict.fromkeys(result['warnings']))
        return result

    def flag(self, history_id, summaries, body):
        view = self.view(history_id, summaries)
        summary = next((s for s in summaries if s.id == view['session'].get('id')), None) if view['attachment'] == 'attached' else None
        return self.store.save_flag(view, body, summary)
