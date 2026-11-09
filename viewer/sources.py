"""Conservative source linkage over captured records only; never opens source paths."""
import json
import re
import shlex


def single_read(call, title):
    """Recognize only a single literal cat command, never shell programs/batches."""
    if call['role'] not in ('exec_command', 'functions.exec_command'):
        return False
    try:
        arguments = json.loads(call['text'])
        command = arguments.get('cmd', '')
        if not isinstance(command, str) or re.search(r'[;&|<>`$\n\r*?{}()\\]', command):
            return False
        words = shlex.split(command)
        return len(words) == 2 and words[0] == 'cat' and not words[1].startswith('-') and words[1] == title
    except (ValueError, AttributeError):
        return False


def match_record(key, by_key, calls, title):
    record = by_key.get(key)
    result = dict(key=key, status='unresolved', text='', coverage='unknown', observed_at=None,
                  content_kind='Unknown source content', call_key=None, call_text=None,
                  version='Unknown; captured record is not a current file version')
    if not record:
        return result
    result.update(text=record['text'], observed_at=record['observed_at'])
    if re.search(r'truncat(?:ed|ion)|tokens truncated', record['text'], re.I):
        result['coverage'] = 'truncated'
    if record['kind'] == 'supplied':
        result['status'] = 'recorded-supplied'
        result['content_kind'] = 'Recorded supplied content (may include instructions)'
        if '## Skills' in record['text'] or 'Available skills' in record['text']:
            result['content_kind'] = 'Supplied skills catalog; entries do not establish recorded skill content'
    elif record['kind'] == 'tool_output':
        paired = calls.get(record.get('call_id'), []) if record.get('call_id') else []
        outputs = [r for r in by_key.values() if r['kind'] == 'tool_output' and r.get('call_id') == record.get('call_id')]
        if len(paired) == 1 and len(outputs) == 1:
            call = paired[0]
            result.update(call_key=call['key'], call_text=call['text'])
            ordered = list(by_key).index(call['key']) < list(by_key).index(key)
            # Status belongs to the execution wrapper, never to captured stdout.
            parts = re.split(r'^Output:[ \t]*$', record['text'], maxsplit=1, flags=re.M)
            statuses = re.findall(r'^Process exited with code ([0-9]+)[ \t]*$', parts[0], re.M)
            success = len(parts) == 2 and statuses == ['0']
            if ordered and success and single_read(call, title):
                result['status'] = 'recorded-read'
                result['content_kind'] = 'Captured output paired with one literal cat read'
            else:
                result['content_kind'] = 'Captured tool output; source attribution ambiguous'
    return result


def project_sources(snapshot, records):
    by_key = {r['key']: r for r in records}
    calls = {}
    for record in records:
        if record['kind'] == 'tool_call' and record.get('call_id'):
            calls.setdefault(record['call_id'], []).append(record)
    sources, assigned = [], set()
    for source in (snapshot or {}).get('sources', []):
        claims = [c for c in snapshot['claims'] if source['id'] in c['source_ids']]
        matches = [match_record(key, by_key, calls, source['title']) for key in source['evidence_keys']]
        assigned.update(source['evidence_keys'])
        sources.append(dict(**source, matches=matches, claims=[c['id'] for c in claims],
                            scopes=sorted({c['scope'] for c in claims}), origin='snapshot-reported reference',
                            relevance='Agent-linked claims' if claims else 'Unknown; no linked claim'))
    # References absent from the source collection remain inspectable and explicitly unresolved.
    known = {s['id'] for s in sources}
    for claim in (snapshot or {}).get('claims', []):
        for source_id in claim['source_ids']:
            if source_id not in known:
                related = [c for c in snapshot['claims'] if source_id in c['source_ids']]
                sources.append(dict(id=source_id, title='Undefined source reference', evidence_keys=[], matches=[],
                                    claims=[c['id'] for c in related], scopes=sorted({c['scope'] for c in related}),
                                    origin='snapshot-reported reference', relevance='Unresolved reference'))
                known.add(source_id)
    for record in records:
        if record['key'] not in assigned and record['kind'] in ('supplied', 'tool_output'):
            sources.append(dict(id=None, title=f"Unassigned {record['kind']}", evidence_keys=[record['key']],
                                matches=[match_record(record['key'], by_key, calls, '')], claims=[], scopes=[],
                                origin='recorded evidence', relevance='Unknown; no linked claim'))
    # Snapshot references may repeat the same large record many times. Bound the
    # serialized excerpts, not only the underlying selected-log cache.
    remaining = 256 * 1024
    for source in sources:
        for match in source['matches']:
            match['display_note'] = ''
            for field in ('text', 'call_text'):
                text = match.get(field) or ''
                limit = min(8192, remaining)
                if len(text) > limit:
                    match[field] = text[:limit]
                    match['display_note'] = 'Display excerpt limited; inspect raw evidence pages for retained content.'
                    if match['coverage'] != 'truncated':
                        match['coverage'] = 'excerpt'
                remaining -= min(len(text), limit)
    return sources
