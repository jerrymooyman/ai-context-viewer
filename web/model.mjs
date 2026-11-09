export function selection() {
  return { contextId: null, request: 0, view: null, scope: 'goal' };
}

export function beginSelection(state, contextId) {
  state.contextId = contextId;
  state.view = null;
  state.scope = 'goal';
  return { contextId, request: ++state.request };
}

export function beginPoll(state) {
  return { contextId: state.contextId, request: ++state.request };
}

export function acceptView(state, token, view) {
  if (token.request !== state.request || token.contextId !== state.contextId) return false;
  state.view = view;
  return true;
}

export function renderRecords(doc, container, records, onBoundary) {
  const nodes = records.map(record => {
    const details = doc.createElement('details');
    details.className = 'record';
    const summary = doc.createElement('summary');
    summary.textContent = `${record.role} / ${record.kind.replaceAll('_', ' ')}`;
    const meta = doc.createElement('small');
    meta.textContent = `${record.observed_at ?? 'Time unknown'} — coverage: ${record.coverage}`;
    const key = doc.createElement('small');
    key.textContent = `Record ${record.key}`;
    const text = doc.createElement('pre');
    text.textContent = record.text;
    details.append(summary, meta, key, text);
    if (record.warning) {
      const warning = doc.createElement('p');
      warning.className = 'warning';
      warning.textContent = record.warning;
      details.append(warning);
    }
    if (onBoundary) {
      const button = doc.createElement('button');
      button.type = 'button';
      button.textContent = 'Start new context before this record';
      button.addEventListener('click', () => onBoundary(record.key));
      details.append(button);
    }
    return details;
  });
  if (!nodes.length) {
    const empty = doc.createElement('p');
    empty.className = 'muted';
    empty.textContent = 'No records captured in this view.';
    nodes.push(empty);
  }
  container.replaceChildren(...nodes);
}

export function snapshotAge(timestamp, now = Date.now()) {
  const time = Date.parse(timestamp);
  if (!Number.isFinite(time)) return 'Observation time unknown';
  if (time > now) return 'Recorded time is in the future';
  const seconds = Math.floor((now - time) / 1000);
  if (seconds < 60) return `${seconds} seconds ago`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes} minute${minutes === 1 ? '' : 's'} ago`;
  const hours = Math.floor(minutes / 60);
  return `${hours} hour${hours === 1 ? '' : 's'} ago`;
}

export function snapshotModel(view, selected = 'goal', now = Date.now()) {
  const snapshot = view.snapshot;
  const errors = view.snapshot_errors || [];
  if (!snapshot) return { empty: true, errors, scope: 'goal' };
  const scope = snapshot.subtasks.some(task => task.id === selected) ? selected : 'goal';
  const head = view.snapshot_heads?.[scope];
  const entry = view.current_report?.snapshot.snapshot_id === head ? view.current_report
    : view.snapshot_history?.find(item => item.snapshot.snapshot_id === head);
  const claims = snapshot.claims.filter(claim => claim.scope === scope);
  const tasks = snapshot.subtasks.map(task => ({ ...task, verification:
    snapshot.claims.some(claim => claim.scope === task.id && claim.verification.status !== 'unknown')
      ? 'Agent-reported result; not independently verified' : 'Unknown; no independent verification' }));
  return { empty: false, errors, scope, constraints: snapshot.goal.constraints, questions: snapshot.goal.questions,
    objective: snapshot.goal.objective, tasks, claims, origin: entry?.origin || 'Unknown',
    observedAt: entry?.observed_at, age: snapshotAge(entry?.observed_at, now), evidenceKey: entry?.evidence_key };
}

export function snapshotRequest(example) {
  return `Before executing, state your task understanding as a fenced context-viewer-snapshot JSON block using schema v1 below. Replace the example with this context's objective, shared constraints, unresolved questions, stable subtask IDs, assumptions and reported source references. Use only these exact fields. All fields are required. IDs use letters, digits, _, ., :, or - (1–128 characters). Keep the JSON under 64 KiB; each list is at most 256 items and each text field at most 8192 characters.
Scope is "goal" or one existing subtask ID. The first snapshot must be goal scope with supersedes: null. Later goal updates supersede the current goal snapshot and replace its entire subtask/claim/source collection. A subtask update uses goal: null, exactly that one subtask, only its claims, and supersedes its current snapshot ID; shared constraints and other scopes remain unchanged. Retain goal_id within this context; choose a new snapshot_id each time. Dependencies must refer to existing subtasks without cycles.
Progress: planned, in_progress, blocked, done. Claim kind: assumption, constraint, decision, question. Verification status: unknown, reported_pass, reported_fail. Your verification report is not independent verification. Use unknown when unsupported. Source IDs and evidence keys may be unresolved; never invent a read, source content, or successful check. Do not add timestamps; the viewer uses the log envelope or local import receipt.
\n\`\`\`context-viewer-snapshot\n${JSON.stringify(example, null, 2)}\n\`\`\``;
}

export function scopedSources(view, scope) {
  return (view.sources || []).filter(source => source.scopes.includes(scope));
}

export function correctionText(view, scope, claim, source) {
  const excerpts = (source?.matches || []).map(match =>
    `Record ${match.key} (${match.status}; coverage ${match.coverage || 'unknown'}):\n${match.text || 'Captured content unavailable.'}`).join('\n\n');
  return `Clarification for context ${view.id}\nGoal: ${view.snapshot?.goal_id || 'Unknown'}\nScope: ${scope === 'goal' ? 'shared goal' : 'subtask ' + scope}\nClaim: ${claim?.id || 'No claim selected'}\nSource: ${source?.id || 'Unassigned evidence'} — ${source?.title || 'Unknown'}\n\nDisputed statement:\n${claim?.statement || 'Please review this source’s applicability to the selected scope.'}\n\nSupporting captured excerpt (historical exposure; not independent verification):\n${excerpts || 'No resolved captured excerpt. Please verify the reference.'}\n\nMy correction and requested check:\n[Describe the correction and check whether it applies to this scope. Cite evidence and report any unresolved uncertainty in a new scoped snapshot.]\n\nAcknowledgment and corrected behavior need separate evidence. Please state what you accepted and report the check result; do not treat this request alone as verification.`;
}

export async function copyEditable(editor, clipboard) {
  try {
    await clipboard.writeText(editor.value);
    return 'Copied text. Sending: unknown. Acknowledgment: unknown. Behavior verified: unknown.';
  } catch {
    editor.focus(); editor.select();
    return 'Select and copy the text manually. Sending: unknown. Acknowledgment: unknown. Behavior verified: unknown.';
  }
}

// Input order is newest first; Map preserves each project's first appearance.
export function sessionGroups(sessions) {
  const groups = new Map();
  for (const session of sessions) {
    const project = session.project || '';
    if (!groups.has(project)) groups.set(project, []);
    groups.get(project).push(session);
  }
  return [...groups].map(([project, sessions]) => ({ project, sessions }));
}

export function taskGroups(histories) {
  const groups = new Map();
  for (const history of histories) {
    if (!groups.has(history.task_ref)) groups.set(history.task_ref, []);
    groups.get(history.task_ref).push(history);
  }
  return [...groups].map(([task, histories]) => ({task, histories}));
}

// Calendar days use the browser's local timezone, not UTC date slicing.
export function dateGroups(items, timestamp, now = new Date()) {
  const keyFor = date => `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}`;
  const today = keyFor(now);
  const groups = new Map();
  for (const item of items) {
    const raw = timestamp(item);
    const date = raw == null || raw === '' ? null : new Date(raw);
    const valid = date && Number.isFinite(date.getTime());
    const key = valid ? keyFor(date) : 'unknown';
    if (!groups.has(key)) groups.set(key, {key, label: valid ? (key === today ? 'Today' : date.toLocaleDateString(undefined, {year:'numeric', month:'short', day:'numeric'})) : 'Date unknown', today: key === today, items: []});
    groups.get(key).items.push(item);
  }
  return [...groups.values()].sort((a, b) => a.key === 'unknown' ? 1 : b.key === 'unknown' ? -1 : b.key.localeCompare(a.key));
}

export function capturedSourceText(view, source) {
  return `${source.title}\nContext: ${view.id}\nOrigin: ${source.origin}\nHistorical captured evidence; this is not the current source file.\n\n${source.matches.map(match =>
    `Record ${match.key} · ${match.status}\nObserved: ${match.observed_at || 'Unknown'}\nCoverage: ${match.coverage}\nVersion: ${match.version}\n${match.display_note || ''}\n\n${match.text || 'Captured content unavailable.'}${match.call_key ? '\n\nPaired call ' + match.call_key + '\n' + match.call_text : ''}`).join('\n\n---\n\n') || 'No captured content available; coverage unknown.'}`;
}
