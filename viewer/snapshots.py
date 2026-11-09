"""Strict self-report schema and atomic, context-scoped snapshot projection."""
from copy import deepcopy
from dataclasses import dataclass, field
import json
import re

MAX_SNAPSHOT_BYTES = 65536
MAX_HISTORY = 128
ID = re.compile(r'^[A-Za-z0-9_.:-]{1,128}$')


@dataclass
class ValidationResult:
    valid: bool
    errors: list[str] = field(default_factory=list)


@dataclass
class SnapshotHistory:
    context_key: str | None = None
    current: dict | None = None
    history: list = field(default_factory=list)
    heads: dict = field(default_factory=dict)


def parse_snapshot(text):
    if len(text.encode('utf-8')) > MAX_SNAPSHOT_BYTES:
        raise ValueError('Snapshot exceeds 64 KiB.')
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError(f'Duplicate JSON key: {key}')
            result[key] = value
        return result
    def constant(_):
        raise ValueError('Nonfinite JSON numbers are unsupported.')
    try:
        return json.loads(text, object_pairs_hook=pairs, parse_constant=constant)
    except (RecursionError, UnicodeError) as error:
        raise ValueError('Snapshot JSON nesting or encoding is invalid.') from error


def require(condition, message):
    if not condition:
        raise ValueError(message)


def identifier(value):
    return isinstance(value, str) and bool(ID.fullmatch(value))


def text(value):
    return isinstance(value, str) and 0 < len(value) <= 8192


def array(value):
    return isinstance(value, list) and len(value) <= 256


def strings(value, predicate=text):
    return array(value) and all(predicate(v) for v in value)


def fields(value, keys):
    return isinstance(value, dict) and set(value) == set(keys.split())


def unique(items):
    return len(items) == len(set(items))


def validate_snapshot(payload):
    try:
        require(fields(payload, 'schema_version snapshot_id goal_id scope supersedes goal subtasks claims sources'), 'Expected exactly the schema v1 fields.')
        require(type(payload['schema_version']) is int and payload['schema_version'] == 1, 'Unsupported schema_version; expected 1.')
        require(all(identifier(payload[k]) for k in ('snapshot_id', 'goal_id', 'scope')), 'Snapshot, goal and scope IDs are required.')
        require(payload['supersedes'] is None or identifier(payload['supersedes']), 'supersedes must be null or a snapshot ID.')
        scope = payload['scope']
        goal = payload['goal']
        if scope == 'goal':
            require(fields(goal, 'objective constraints questions') and text(goal['objective'])
                    and strings(goal['constraints']) and strings(goal['questions']), 'Goal requires objective, constraints and questions.')
        else:
            require(goal is None, 'Subtask updates require goal: null; shared goal content is preserved.')
        for name in ('subtasks', 'claims', 'sources'):
            require(array(payload[name]), f'{name} must be an array of at most 256 items.')
        subtasks = payload['subtasks']
        for task in subtasks:
            require(fields(task, 'id title dependencies progress') and identifier(task['id']) and task['id'] != 'goal'
                    and text(task['title']) and strings(task['dependencies'], identifier)
                    and unique(task['dependencies']) and task['progress'] in ('planned', 'in_progress', 'blocked', 'done'), 'Invalid subtask fields, dependencies or reported progress.')
        task_ids = [t['id'] for t in subtasks]
        require(unique(task_ids), 'Subtask IDs must be unique.')
        if scope != 'goal':
            require(task_ids == [scope], 'A subtask snapshot must contain only its declared subtask.')
        else:
            check_dependencies(subtasks)
        for claim in payload['claims']:
            require(fields(claim, 'id scope statement kind source_ids verification') and identifier(claim['id'])
                    and identifier(claim['scope']) and text(claim['statement'])
                    and claim['kind'] in ('assumption', 'constraint', 'decision', 'question')
                    and strings(claim['source_ids'], identifier), 'Invalid claim fields.')
            require(claim['scope'] in (['goal'] + task_ids if scope == 'goal' else [scope]), 'Claim scope is outside this snapshot.')
            verification = claim['verification']
            require(fields(verification, 'status evidence_keys') and verification['status'] in ('unknown', 'reported_pass', 'reported_fail')
                    and strings(verification['evidence_keys']), 'Verification must remain unknown or agent-reported, with evidence_keys.')
        require(unique([c['id'] for c in payload['claims']]), 'Claim IDs must be unique.')
        for source in payload['sources']:
            require(fields(source, 'id title evidence_keys') and identifier(source['id']) and text(source['title'])
                    and strings(source['evidence_keys']), 'Invalid source reference fields.')
        require(unique([s['id'] for s in payload['sources']]), 'Source IDs must be unique.')
        require(len(json.dumps(payload, ensure_ascii=False, allow_nan=False).encode()) <= MAX_SNAPSHOT_BYTES, 'Snapshot exceeds 64 KiB.')
        return ValidationResult(True)
    except (ValueError, TypeError, RecursionError) as error:
        return ValidationResult(False, [str(error)])


def check_dependencies(subtasks):
    graph = {t['id']: t['dependencies'] for t in subtasks}
    visited, visiting = set(), set()
    def visit(node):
        require(node in graph, 'Unknown dependency reference.')
        require(node not in visiting, 'Dependency cycle.')
        if node in visited:
            return
        visiting.add(node)
        for dependency in graph[node]:
            visit(dependency)
        visiting.remove(node)
        visited.add(node)
    for node in graph:
        visit(node)


def apply_snapshot(history, payload, context_key, *, observed_at=None, origin='agent-self-report', evidence_key=None):
    result = validate_snapshot(payload)
    if not result.valid:
        return result
    try:
        require(history.context_key in (None, context_key), 'Cross-context supersession is forbidden.')
        require(len(history.history) < MAX_HISTORY, 'Snapshot history capacity reached (128); last valid snapshot retained.')
        require(payload['snapshot_id'] not in {h['snapshot']['snapshot_id'] for h in history.history}, 'Duplicate snapshot_id.')
        scope = payload['scope']
        if history.current is None:
            require(scope == 'goal' and payload['supersedes'] is None, 'First snapshot must be a goal with supersedes: null.')
        else:
            require(payload['goal_id'] == history.current['goal_id'], 'Goal ID cannot change within a context.')
            require(scope in history.heads and payload['supersedes'] == history.heads[scope], 'Supersession must reference the current snapshot for this scope.')
        if scope == 'goal':
            current = deepcopy(payload)
            heads = {key: payload['snapshot_id'] for key in ['goal'] + [t['id'] for t in payload['subtasks']]}
        else:
            current = deepcopy(history.current)
            current['subtasks'] = [deepcopy(payload['subtasks'][0]) if t['id'] == scope else t for t in current['subtasks']]
            current['claims'] = [c for c in current['claims'] if c['scope'] != scope] + deepcopy(payload['claims'])
            require(unique([c['id'] for c in current['claims']]), 'Claim ID conflicts with another scope.')
            sources = {s['id']: s for s in current['sources']}
            for source in payload['sources']:
                require(source['id'] not in sources or sources[source['id']] == source, 'Source ID conflicts with an existing definition; use a new ID or a goal update.')
                sources[source['id']] = deepcopy(source)
            current['sources'] = list(sources.values())
            check_dependencies(current['subtasks'])
            heads = dict(history.heads, **{scope: payload['snapshot_id']})
        history.context_key = context_key
        history.current = current
        history.heads = heads
        history.history.append(dict(snapshot=deepcopy(payload), observed_at=observed_at, origin=origin, evidence_key=evidence_key))
        return ValidationResult(True)
    except (ValueError, TypeError, RecursionError) as error:
        return ValidationResult(False, [str(error)])


# A marker is recognized only on its own line in an assistant message.
BLOCK = re.compile(r'^```context-viewer-snapshot[ \t]*\r?\n(.*?)^```[ \t]*\r?$', re.MULTILINE | re.DOTALL)


def extract_snapshots(recorded):
    payload = recorded.record.get('payload', {})
    if (recorded.record.get('type') != 'response_item' or not isinstance(payload, dict)
            or payload.get('type') != 'message' or payload.get('role') != 'assistant'):
        return []
    content = payload.get('content', [])
    if not isinstance(content, list):
        return []
    message = '\n'.join(p['text'] for p in content if isinstance(p, dict) and isinstance(p.get('text'), str))
    found = [m.group(1) for m in BLOCK.finditer(message)]
    if not found and re.search(r'^```context-viewer-snapshot[ \t]*$', message, re.MULTILINE):
        return ['']  # Incomplete/invalid fenced block gets a visible validation error.
    return found
