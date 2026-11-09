"""Adapter for captured 0.167.0 records; historical exposure is not residency."""
from dataclasses import dataclass
import json
from .logs import RecordKey


@dataclass
class Evidence:
    key: str
    kind: str
    role: str
    text: str
    observed_at: str | None
    coverage: str = 'unknown'
    call_id: str | None = None
    warning: str = ''


def captured(value):
    return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)


def normalize(record: dict, key: RecordKey) -> list[Evidence]:
    payload = record.get('payload', {})
    timestamp = record.get('timestamp')
    timestamp = timestamp if isinstance(timestamp, str) else None
    def evidence(kind, role, text, call_id=None, warning=''):
        return [Evidence(str(key), kind, role, text, timestamp, call_id=call_id, warning=warning)]
    if not isinstance(payload, dict):
        return evidence('unknown', '', captured(payload), warning='Unsupported record payload.')
    record_type = record.get('type')
    if record_type == 'session_meta':
        base = payload.get('base_instructions')
        if isinstance(base, dict):
            base = base.get('text')
        return evidence('supplied', 'base instructions', base) if isinstance(base, str) else []
    if record_type == 'response_item':
        item_type = payload.get('type')
        if item_type == 'message':
            role = payload.get('role', 'unknown')
            content = payload.get('content', [])
            if not isinstance(content, list):
                content = [content]
            texts = []
            for part in content:
                if isinstance(part, dict) and isinstance(part.get('text'), str):
                    texts.append(part['text'])
                else:
                    texts.append('[Unsupported content part] ' + captured(part))
            kind = 'supplied' if role in ('system', 'developer', 'user') else 'agent_message'
            return evidence(kind, captured(role), '\n'.join(texts))
        if item_type in ('function_call', 'custom_tool_call'):
            return evidence('tool_call', captured(payload.get('name', 'tool')), captured(payload.get('arguments', payload.get('input', ''))), payload.get('call_id'))
        if item_type in ('function_call_output', 'custom_tool_call_output'):
            return evidence('tool_output', 'tool', captured(payload.get('output', '')), payload.get('call_id'))
    if record_type == 'event_msg':
        return evidence('activity', 'recorded event', captured(payload))
    if record_type in ('turn_context', 'compacted'):
        return evidence('activity', str(record_type), captured(payload), warning='Recorded event does not establish retained prompt contents.')
    return evidence('unknown', str(record_type), captured(payload), warning='Unsupported record type; provenance unresolved.')
