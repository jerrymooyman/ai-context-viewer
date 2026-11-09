import { selection, beginSelection, beginPoll, acceptView, renderRecords, snapshotModel, snapshotRequest, scopedSources, correctionText, copyEditable, dateGroups, capturedSourceText, taskGroups } from './model.mjs';

const state = selection();
const element = id => document.getElementById(id);
const value = item => typeof item === 'string' ? item : JSON.stringify(item);

async function get(path) {
  const response = await fetch(path, { cache: 'no-store', mode: 'same-origin' });
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || `Request failed (${response.status})`);
  return data;
}

function markSelection() {
  for (const button of element('sessions').querySelectorAll('button.session')) {
    button.setAttribute('aria-current', String(button.dataset.contextId === state.contextId));
  }
}

let pageAfter = -1;
let historyCursor = null;
let fileHistories = [];
let pairedFileId = null;
const dateDisclosure = new Map();
let listing = false;
let listedSessions = [];

async function boundary(recordKey) {
  const selectionId = state.contextId;
  const contextId = state.view?.evidence_context_id || state.contextId;
  try {
    const response = await fetch(`/api/contexts/${encodeURIComponent(contextId)}/boundaries`, {
      method: 'POST', mode: 'same-origin', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(recordKey ? { record_key: recordKey } : { action: 'remove' }),
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error);
    if (state.contextId !== selectionId) return;
    await refresh();
    if (state.view?.stream === 'files') await choose(selectionId, true);
    else await choose(result.context_id);
  } catch (error) { element('boundary-status').textContent = error.message; }
}

function clearDrafts() {
  element('correction-panel').hidden = true;
  element('correction-text').value = '';
  element('import-json').value = '';
  element('import-status').textContent = '';
}

async function choose(id, poll = false) {
  if (!poll) {
    pageAfter = -1;
    historyCursor = null;
    clearDrafts();
  }
  const token = poll ? beginPoll(state) : beginSelection(state, id);
  markSelection();
  if (!poll) {
    element('detail').hidden = true;
    element('title').textContent = 'Loading context…';
    element('view-status').textContent = 'Reading captured evidence…';
  }
  try {
    const isFile = fileHistories.some(item => item.history_id === id);
    const view = await get(isFile
      ? `/api/file-histories/${encodeURIComponent(id)}?after=${pageAfter}${historyCursor ? '&cursor=' + encodeURIComponent(historyCursor) : ''}`
      : `/api/contexts/${encodeURIComponent(id)}?after=${pageAfter}`);
    if (token.request !== state.request || token.contextId !== state.contextId) return;
    if (poll && state.view && (state.view.generation || 'unavailable-log') !== (view.generation || 'unavailable-log')) {
      clearDrafts();
      state.scope = 'goal';
      if (pageAfter !== -1) {
        pageAfter = -1;
        return choose(id, true);
      }
    }
    element('understanding-stream').value = isFile ? 'files' : 'log-import';
    element('understanding-stream').options[0].disabled = !isFile && !pairedFileId;
    element('understanding-stream').options[1].disabled = isFile && !view.evidence_context_id;
    element('import-panel').hidden = isFile;
    element('file-provenance').textContent = isFile ? `Task: ${view.task_ref || 'Unassigned task'} · ${view.attachment}. Exported ${view.snapshot_at}. Accepted ${view.current_report.accepted_at}. ${view.current_report.status}. Files: ${view.current_report.source_names.join(', ')} (${view.current_report.source_count} total).` : 'Log and temporary imports have a separate understanding chain.';
    if (isFile && view.cross_stream.length) element('file-provenance').textContent += ' ' + view.cross_stream.map(item => `${item.report_id}: ${item.status === 'linked' ? 'Same report also recorded in ' : 'Different content recorded in '}${item.origin}${item.evidence_key ? ' (record ' + item.evidence_key + ')' : ''}.`).join(' ');
    element('context-target-panel').hidden = !view.context_target;
    element('context-target').value = view.context_target ? JSON.stringify(view.context_target, null, 2) : '';
    element('view-status').textContent = `Input: ${view.status}. Collection: ${view.collection_status || 'disabled'}. Manual refresh.`;
    updateSnapshotTime(view);
    if (poll && state.view?.source_revision === view.source_revision && state.view?.snapshot_revision === view.snapshot_revision && state.view?.revision === view.revision && state.view?.status === view.status && state.view?.page_after === pageAfter && state.view?.history_cursor === historyCursor
        && JSON.stringify(state.view?.warnings) === JSON.stringify(view.warnings)) return;
    const previousSnapshotRevision = state.view?.snapshot_revision;
    view.page_after = pageAfter;
    view.history_cursor = historyCursor;
    if (!acceptView(state, token, view)) return;
    element('title').textContent = view.session.project || view.session.session_id || 'Recorded session';

    const metadata = [
      ['Session', view.session.session_id || 'Unknown'],
      ['Recorded version', view.session.recorded_version || 'Unknown'],
      ['Recorded source label', value(view.session.recorded_source) ?? 'Unknown'],
      ['Recorded originator', view.session.originator || 'Unknown'],
      ['Client identity', 'Unverified; recorded labels are preserved without inference.'],
      ['Last captured activity', view.activity_at || 'Unknown'],
      ['Latest valid snapshot observation', view.snapshot_at || 'Unknown / no valid snapshot'],
      ['Context boundary', view.boundary ? `User declared at byte ${view.boundary.offset}; ${view.boundary.created_at}` : 'Initial recorded context'],
    ];
    const terms = metadata.flatMap(([label, text]) => {
      const dt = document.createElement('dt'); dt.textContent = label;
      const dd = document.createElement('dd'); dd.textContent = text;
      return [dt, dd];
    });
    element('metadata').replaceChildren(...terms);
    element('warnings').replaceChildren(...view.warnings.map(text => {
      const li = document.createElement('li'); li.textContent = text; return li;
    }));
    const onBoundary = view.status === 'supported' || view.attachment === 'attached' ? boundary : null;
    renderRecords(document, element('supplied'), view.supplied, onBoundary);
    renderRecords(document, element('activity'), view.activity, onBoundary);
    element('next-page').disabled = view.next === null;
    element('first-page').disabled = pageAfter === -1;
    element('remove-boundary').hidden = !view.boundary;
    element('boundary-status').textContent = '';
    element('page-status').textContent = `Evidence page after byte ${pageAfter}; ${view.next === null ? 'no later captured page' : 'more evidence available'}.`;
    if (!poll || previousSnapshotRevision !== view.snapshot_revision || view.stream === 'files') renderSnapshot(view);
    else renderSources(view);
    element('detail').hidden = false;
    if (!poll) element('title').focus();
  } catch (error) {
    if (token.request !== state.request) return;
    element('detail').hidden = true;
    element('title').textContent = 'Context unavailable';
    element('view-status').textContent = `${error.message} Refresh the list or select the context again to retry.`;
  }
}

function dateSections(items, timestamp, key, render) {
  return dateGroups(items, timestamp).map(group => {
    const details = node('details', undefined, 'session-date');
    const id = `${key}:${group.key}`;
    details.open = dateDisclosure.has(id) ? dateDisclosure.get(id) : group.today;
    details.append(node('summary', `${group.label} · ${group.items.length}`));
    details.append(...group.items.flatMap(render));
    details.addEventListener('toggle', () => { if (details.isConnected) dateDisclosure.set(id, details.open); });
    return details;
  });
}

function renderSessionList() {
  const fileNodes = taskGroups(fileHistories).map(group => {
    const container = node('section');
    container.append(node('h3', group.task || 'Unassigned task', 'project-heading'));
    const sessions = new Map();
    for (const history of group.histories) {
      if (!sessions.has(history.session_id)) sessions.set(history.session_id, []);
      sessions.get(history.session_id).push(history);
    }
    const items = [...sessions].map(([sessionId, histories]) => ({sessionId, histories,
      timestamp: histories.map(h => h.exported_at).sort().at(-1)}));
    container.append(...dateSections(items, item => item.timestamp, `files:${group.task}`, item => {
      const heading = node('h4', `Session ${item.sessionId}`);
      return [heading, ...item.histories.map(history => {
        const button = node('button', undefined, 'session');
        button.type = 'button'; button.dataset.contextId = history.history_id;
        button.append(node('strong', history.context_binding.kind === 'manual' ? `Declared context ${history.context_binding.context_id}` : 'Initial context'),
          node('small', `${history.count} saved report(s) · ${history.retained ? 'retained history' : 'saved files'}`));
        button.addEventListener('click', () => { pairedFileId = history.history_id; choose(history.history_id); });
        return button;
      })];
    }));
    return container;
  });
  const nodes = dateSections(listedSessions, session => session.modified_at == null ? null : session.modified_at * 1000, 'logs', session =>
    (session.contexts || [{id: session.id, label: 'Initial context'}]).map(context => {
      const button = node('button', undefined, 'session');
      button.type = 'button'; button.dataset.contextId = context.id;
      button.append(node('strong', session.project || session.session_id || 'Metadata unavailable'),
        node('small', `${context.label} / Version ${session.recorded_version || 'unknown'} / ${session.status}`),
        node('small', `File modified: ${session.modified_at != null ? new Date(session.modified_at * 1000).toLocaleString() : 'unknown'}`));
      button.addEventListener('click', () => {
        const file = fileHistories.filter(h => h.session_id === session.session_id && (h.context_binding.kind === 'initial' ? !context.boundary : h.context_binding.context_id === context.id));
        pairedFileId = file.length === 1 ? file[0].history_id : null;
        choose(pairedFileId || context.id);
      });
      return button;
    }));
  element('sessions').replaceChildren(...fileNodes, ...(nodes.length ? [node('h3', 'Recorded log / import contexts'), ...nodes] : []));
  markSelection();
}



async function refresh() {
  if (listing) return;
  listing = true;
  element('refresh').disabled = true;
  element('list-status').textContent = 'Loading contexts…';
  try {
    historyCursor = null;
    const data = await post('/api/collect', '{}');
    listedSessions = data.sessions;
    fileHistories = data.file_histories || [];
    if (pairedFileId && !fileHistories.some(h => h.history_id === pairedFileId)) pairedFileId = null;
    renderSessionList();
    element('list-status').textContent = `${fileHistories.length} file histories · ${data.sessions.length} recorded sessions. Choose explicitly. ${data.warnings.join(' ')}`;
    if (state.contextId && !fileHistories.some(h => h.history_id === state.contextId) && !data.sessions.some(session => (session.contexts || [session]).some(context => context.id === state.contextId))) {
      beginSelection(state, null);
      element('detail').hidden = true;
      element('title').textContent = 'Context unavailable';
      element('view-status').textContent = 'The selected session is no longer in the list. Choose another context.';
    }
  } catch (error) {
    element('list-status').textContent = `${error.message} Use Refresh to retry.`;
  } finally {
    element('refresh').disabled = false;
    listing = false;
  }
}

element('understanding-stream').addEventListener('change', async event => {
  if (event.target.value === 'files' && pairedFileId) await choose(pairedFileId);
  else if (state.view?.evidence_context_id) { pairedFileId = state.contextId; await choose(state.view.evidence_context_id); }
});
element('copy-target').addEventListener('click', async () => {
  try { await navigator.clipboard.writeText(element('context-target').value); element('target-status').textContent = 'Copied explicit target. No message sent.'; }
  catch { element('context-target').focus(); element('context-target').select(); element('target-status').textContent = 'Select and copy the target manually. No message sent.'; }
});
element('history-next').addEventListener('click', () => { if (state.view?.history_next) { historyCursor = state.view.history_next; choose(state.contextId, true); } });
element('history-first').addEventListener('click', () => { historyCursor = null; choose(state.contextId, true); });
element('next-page').addEventListener('click', () => {
  if (state.view?.next !== null) { pageAfter = state.view.next; choose(state.contextId, true); }
});
element('first-page').addEventListener('click', () => { pageAfter = -1; choose(state.contextId, true); });
element('remove-boundary').addEventListener('click', () => boundary(null));
element('refresh').addEventListener('click', async () => {
  if (listing) return;
  element('refresh').disabled = true;
  try {
    await refresh();
    element('refresh').disabled = true;
    if (state.contextId) await choose(state.contextId, true);
  } finally { element('refresh').disabled = false; }
});
refresh();


function node(tag, text, className) {
  const item = document.createElement(tag);
  if (text !== undefined) item.textContent = text;
  if (className) item.className = className;
  return item;
}

function updateSnapshotTime(view = state.view) {
  if (!view) return;
  const model = snapshotModel(view, state.scope);
  element('snapshot-time').textContent = model.empty ? 'No valid snapshot observation.'
    : `${model.scope === 'goal' ? 'Goal' : 'Subtask ' + model.scope} snapshot: ${model.observedAt || 'time unknown'} · ${model.age} · ${model.origin}${model.evidenceKey ? ' · record ' + model.evidenceKey : ' · log provenance unknown'}`;
}

function selectScope(scope) {
  state.scope = scope;
  renderSnapshot(state.view);
}

function renderSnapshot(view) {
  const model = snapshotModel(view, state.scope);
  state.scope = model.scope;
  element('snapshot-empty').hidden = !model.empty;
  element('snapshot-current').hidden = model.empty;
  updateSnapshotTime(view);
  if (!model.empty) {
    element('goal-objective').textContent = model.objective;
    element('shared-constraints').replaceChildren(...(model.constraints.length ? model.constraints : ['No shared constraints reported.']).map(text => node('li', text)));
    const scopes = [{id:'goal',title:'Goal overview'}, ...model.tasks];
    element('subtask-nav').replaceChildren(...scopes.map(task => {
      const button = node('button', task.title, 'subtask-button');
      button.type = 'button'; button.setAttribute('aria-pressed',String(task.id === model.scope));
      button.addEventListener('click', () => selectScope(task.id));
      return button;
    }));
    const content = [];
    if (model.scope === 'goal') {
      const table = node('table', undefined, 'snapshot-table');
      const header = node('tr');
      for (const title of ['Subtask','Reported progress','Verification','Dependencies']) header.append(node('th',title));
      const thead=node('thead'); thead.append(header); table.append(thead);
      const tbody=node('tbody');
      for (const task of model.tasks) {
        const row=node('tr'); const cell=node('td'); const button=node('button',task.title);
        button.type='button'; button.addEventListener('click',()=>selectScope(task.id)); cell.append(button);
        row.append(cell,node('td',task.progress),node('td',task.verification),node('td',task.dependencies.join(', ') || 'None reported'));
        tbody.append(row);
      }
      table.append(tbody);
      const scroll=node('div',undefined,'table-scroll');scroll.append(table);
      content.push(node('h3','Subtasks and reported progress'), scroll);
      content.push(node('h3','Unresolved goal questions'));
      const questions=node('ul');
      for (const question of model.questions.length ? model.questions : ['No unresolved questions reported.']) questions.append(node('li',question));
      content.push(questions);
    } else {
      const task=model.tasks.find(task=>task.id===model.scope);
      content.push(node('h3',task.title), node('p',`Reported progress: ${task.progress}. Verification: ${task.verification}.`),
        node('p',`Dependencies: ${task.dependencies.join(', ') || 'None reported'}. Shared constraints above still apply.`));
    }
    content.push(node('h3',model.scope === 'goal' ? 'Goal understanding' : 'Subtask understanding'));
    for (const claim of model.claims) {
      const card=node('section',undefined,'claim-card');
      card.append(node('strong',`${claim.id} · ${claim.kind}`),node('p',claim.statement),
        node('p',`Verification: ${claim.verification.status} (agent report; not independently verified).`,'muted'),
        node('p',`Evidence references: ${claim.verification.evidence_keys.join(', ') || 'None'} — unresolved.`,'muted'));
      const sourceNames=claim.source_ids.map(id=>{
        const source=view.snapshot.sources.find(source=>source.id===id);
        return source ? `${id}: ${source.title} (agent-reported reference; evidence ${source.evidence_keys.join(', ') || 'unknown'})` : `${id}: unresolved reference`;
      });
      card.append(node('p',`Sources: ${sourceNames.join('; ') || 'Unknown'}`,'muted'));
      for (const id of claim.source_ids) {
        const source=(view.sources || []).find(source=>source.id===id);
        if (!source) continue;
        const button=node('button',`Inspect source ${id}`);button.type='button';
        button.addEventListener('click',()=>{
          const target=[...element('sources').children].find(item=>item.dataset.sourceId===id);
          if (target) {target.open=true;target.scrollIntoView({block:'center'});target.querySelector('summary').focus();}
        });card.append(button);
      }
      const clarify=node('button',`Clarify claim ${claim.id}`);clarify.type='button';
      clarify.addEventListener('click',()=>draftCorrection(view,model.scope,claim,(view.sources || []).find(source=>claim.source_ids.includes(source.id))));
      card.append(clarify);
      content.push(card);
    }
    if (!model.claims.length) content.push(node('p','No claims reported for this scope.','muted'));
    element('snapshot-content').replaceChildren(...content);
  }
  renderSources(view);
  element('history-next').hidden = view.stream !== 'files';
  element('history-first').hidden = view.stream !== 'files';
  element('history-next').disabled = !view.history_next;
  element('history-first').disabled = !historyCursor;
  element('history-status').textContent = view.stream === 'files' ? `${view.snapshot_history.length} of ${view.history_count} saved reports on this page. Current understanding is always the latest accepted report.` : '';
  const errors=view.snapshot_errors || [];
  element('snapshot-errors').hidden=!errors.length;
  element('snapshot-errors').replaceChildren(node('h3','Invalid snapshot attempts — last valid understanding retained'), ...errors.map(error=>{
    const details=node('details');details.append(node('summary',error.error),node('p',`${error.origin} · ${value(error.observed_at) || 'time unknown'}`),node('pre',error.raw));return details;
  }));
  element('snapshot-history').replaceChildren(...(view.snapshot_history || []).map(entry=>{
    const details=node('details');
    const active=Object.entries(view.snapshot_heads || {}).filter(([,id])=>id===entry.snapshot.snapshot_id).map(([scope])=>scope);
    details.append(node('summary',`${entry.snapshot.snapshot_id} · ${entry.snapshot.scope} · ${active.length ? 'current for ' + active.join(', ') : 'superseded / removed'} · ${entry.origin}`),
      node('p',`${entry.observed_at || 'Observation time unknown'} · ${entry.evidence_key || (view.stream === 'files' ? entry.status + '; ' + entry.source_names.join(', ') : 'User import; log provenance unknown')}`),node('pre',JSON.stringify(entry.snapshot,null,2)));
    return details;
  }));
}

async function post(path, body, headers = {}) {
  const response=await fetch(path,{method:'POST',mode:'same-origin',headers:{'Content-Type':'application/json',...headers},body});
  const result=await response.json();
  if (!response.ok) throw new Error(result.error || 'Import failed.');
  return result;
}

element('new-import-context').addEventListener('click',async()=>{
  const previous = state.contextId;
  const button = element('new-import-context');
  button.disabled = true;
  beginSelection(state, null);
  element('detail').hidden = true;
  element('title').textContent = 'Creating import context…';
  markSelection();
  try {
    const result=await post('/api/import-contexts','{}');
    pairedFileId = null;
    await refresh(); await choose(result.context_id);
    element('import-panel').open=true; element('import-json').focus();
  } catch(error) {
    element('list-status').textContent=error.message;
    if (previous) await choose(previous);
  } finally { button.disabled = false; }
});
element('import-form').addEventListener('submit',async event=>{
  event.preventDefault();
  const id=state.contextId;
  const generation=state.view?.generation || 'unavailable-log';
  const raw=element('import-json').value;
  const button=event.target.querySelector('button');button.disabled=true;
  let message='Imported with local receipt time; log provenance unknown. Lost on service restart.';
  try {
    if (new TextEncoder().encode(raw).length>65536) throw new Error('Snapshot exceeds 64 KiB.');
    await post(`/api/contexts/${encodeURIComponent(id)}/imports`,raw,{'X-Context-Generation':generation});
  } catch(error) { message=error.message; }
  finally {
    button.disabled=false;
    if (id===state.contextId) {
      await choose(id,true);
      element('import-status').textContent=message;
    }
  }
});
element('copy-request').addEventListener('click',async()=>{
  try {
    await navigator.clipboard.writeText(element('snapshot-instruction').value);
    element('copy-request-status').textContent='Copied. No message has been sent.';
  } catch {
    element('snapshot-instruction').focus();element('snapshot-instruction').select();
    element('copy-request-status').textContent='Select and copy the request manually. No message has been sent.';
  }
});
get('/snapshot-example.json').then(example=>{element('snapshot-instruction').value=snapshotRequest(example);})
  .catch(error=>{element('snapshot-instruction').value=`Request unavailable: ${error.message}. See README for schema v1.`;});
setInterval(()=>updateSnapshotTime(),1000);

function draftCorrection(view, scope, claim, source) {
  element('correction-panel').hidden=false;
  element('correction-target').textContent=`Context ${view.id} · ${scope === 'goal' ? 'Shared goal' : 'Subtask ' + scope}`;
  element('correction-text').value=correctionText(view,scope,claim,source);
  element('correction-status').textContent='Draft only. Sending: unknown. Acknowledgment: unknown. Behavior verified: unknown.';
  element('correction-text').focus();
}

function renderSources(view) {
  const render = source => {
    const details=node('details',undefined,'record');details.dataset.sourceId=source.id || '';
    details.append(node('summary',`${source.id || 'Unassigned'} · ${source.title}`),
      node('p',`${source.origin} · ${source.relevance}. Linked claims: ${source.claims.join(', ') || 'None'}.`));
    if (source.id) {
      const fileLink = node('a', 'Open source file in new tab');
      const collection = view.stream === 'files' ? 'file-histories' : 'contexts';
      fileLink.href = `/api/${collection}/${encodeURIComponent(view.id)}?source=${encodeURIComponent(source.id)}`;
      fileLink.target = '_blank'; fileLink.rel = 'noopener noreferrer';
      details.append(fileLink, node('p', 'Opens the current local file; it may differ from the captured evidence.', 'muted'));
    }
    const open = node('button', 'Open captured source in new tab');
    open.type = 'button';
    open.addEventListener('click', () => {
      const url = URL.createObjectURL(new Blob([capturedSourceText(view, source)], {type: 'text/plain;charset=utf-8'}));
      window.open(url, '_blank', 'noopener,noreferrer');
      setTimeout(() => URL.revokeObjectURL(url), 60000);
    });
    details.append(open);
    for (const match of source.matches) {
      details.append(node('p',`${match.status} · ${match.content_kind} · ${match.observed_at || 'Time unknown'} · coverage: ${match.coverage}`),
        node('small',`Record ${match.key} · ${match.version}`),node('pre',match.text || 'Captured content unavailable.'));
      if (match.display_note) details.append(node('p',match.display_note,'muted'));
      if (match.call_key) details.append(node('small',`Paired call ${match.call_key}`),node('pre',match.call_text));
    }
    if (!source.matches.length) details.append(node('p','No recorded evidence reference; source content and coverage unknown.'));
    for (const flag of (view.flags || []).filter(flag=>flag.source_id===source.id && (source.id !== null || source.evidence_keys.includes(flag.evidence_key)))) {
      details.append(node('p',`User assessment · ${flag.created_at}: ${flag.reason}`,'warning'));
    }
    const form=node('form');const label=node('label','Your concern (user assessment)');
    const reason=node('textarea');reason.required=true;reason.maxLength=8192;reason.rows=2;
    label.append(reason);const save=node('button','Save concern');save.type='submit';
    const status=node('p');status.setAttribute('role','status');form.append(label,save,status);
    form.addEventListener('submit',async event=>{
      event.preventDefault();save.disabled=true;
      try {
        await post(`/api/${view.stream === 'files' ? 'file-histories' : 'contexts'}/${encodeURIComponent(view.id)}/flags`,JSON.stringify({
          generation:view.generation || 'unavailable-log',source_id:source.id,
          evidence_key:source.id ? null : source.evidence_keys[0],reason:reason.value}));
        status.textContent='Saved user assessment.';reason.value='';
        if (state.contextId===view.id) await choose(view.id,true);
      } catch(error) {status.textContent=error.message;} finally {save.disabled=false;}
    });details.append(form);
    const clarify=node('button','Clarify this source');clarify.type='button';
    clarify.addEventListener('click',()=>draftCorrection(view,state.scope,
      view.snapshot?.claims.find(claim=>claim.scope===state.scope && claim.source_ids.includes(source.id)),source));
    details.append(clarify);return details;
  };
  // Preserve open disclosures and unsaved concerns across activity polling.
  for (const [id,sources] of [['sources',scopedSources(view,state.scope)],['unassigned-sources',(view.sources || []).filter(source=>!source.scopes.length)]]) {
    const container=element(id);
    const signature=JSON.stringify([view.id,view.stream,state.scope,sources,view.flags]);
    if (container.dataset.signature===signature) continue;
    const prior=new Map([...container.children].filter(item=>container.dataset.contextId===view.id && container.dataset.generation===(view.generation || 'unavailable-log') && item.dataset.target).map(item=>[item.dataset.target,{open:item.open,reason:item.querySelector('textarea')?.value}]));
    const nodes=sources.map(source=>{
      const item=render(source);item.dataset.target=source.id || source.evidence_keys[0];
      const saved=prior.get(item.dataset.target);
      if(saved){item.open=saved.open;item.querySelector('textarea').value=saved.reason || '';}
      return item;
    });
    container.replaceChildren(...(nodes.length ? nodes : [node('p','No sources attributed to this scope.','muted')]));
    container.dataset.signature=signature;
    container.dataset.contextId=view.id;
    container.dataset.generation=view.generation || 'unavailable-log';
  }
}

element('copy-correction').addEventListener('click',async()=>{
  const contextId=state.contextId;
  const message=await copyEditable(element('correction-text'),navigator.clipboard);
  if(contextId===state.contextId) element('correction-status').textContent=message;
});
element('correction-text').addEventListener('input',()=>{
  element('correction-status').textContent='Edited draft; copy again to use these changes. Sending, acknowledgment and behavior verification are unknown.';
});
