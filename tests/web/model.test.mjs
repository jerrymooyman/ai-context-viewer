import test from 'node:test';
import assert from 'node:assert/strict';
import { selection, beginSelection, acceptView, renderRecords } from '../../web/model.mjs';

test('refreshing a list never chooses the newest session', () => {
  const state = selection();
  assert.equal(state.contextId, null);
});

test('responses for a previous selection cannot replace the current context', () => {
  const state = selection();
  const a = beginSelection(state, 'a');
  const b = beginSelection(state, 'b');
  assert.equal(acceptView(state, a, { id: 'a' }), false);
  assert.equal(acceptView(state, b, { id: 'b' }), true);
  assert.equal(state.view.id, 'b');
  const repeat = beginSelection(state, 'b');
  assert.equal(acceptView(state, b, { id: 'b', stale: true }), false);
  assert.equal(acceptView(state, repeat, { id: 'b', latest: true }), true);
});

test('hostile captured content renders as literal text nodes', () => {
  // Minimal DOM-shaped document: no HTML parser is available to this renderer.
  class Element {
    constructor(tag) { this.tag = tag; this.children = []; this.textContent = ''; }
    append(...nodes) { this.children.push(...nodes); }
    replaceChildren(...nodes) { this.children = nodes; }
    set innerHTML(_) { throw new Error('HTML interpretation is unsafe'); }
  }
  const doc = { createElement: tag => new Element(tag) };
  const container = new Element('div');
  renderRecords(doc, container, [{ key:'f:0:1',kind:'supplied',role:'user',text:'<img src=x onerror=alert(1)> 🦜', coverage:'unknown',observed_at:null }]);
  const all = function*(node) { yield node; for (const child of node.children) yield* all(child); };
  const nodes = [...all(container)];
  assert.equal(nodes.filter(n => n.tag === 'img').length, 0);
  assert.equal(nodes.find(n => n.tag === 'pre').textContent, '<img src=x onerror=alert(1)> 🦜');
  assert.ok(nodes.some(n => n.textContent.includes('unknown')));
});

test('poll tokens preserve selection and reject an older overlapping refresh', async () => {
  const { beginPoll } = await import('../../web/model.mjs');
  const state = selection();
  beginSelection(state, 'a');
  const old = beginPoll(state);
  const latest = beginPoll(state);
  assert.equal(state.contextId, 'a');
  assert.equal(acceptView(state, latest, { id: 'a' }), true);
  assert.equal(acceptView(state, old, { id: 'a', stale: true }), false);
  beginSelection(state, 'b');
  assert.equal(acceptView(state, latest, { id: 'a' }), false);
});

test('snapshot presentation preserves constraints and separates progress from verification', async () => {
  const { snapshotModel } = await import('../../web/model.mjs');
  const view = {activity_at:'2099-01-01', snapshot_at:'2026-11-08T03:00:00Z', snapshot_heads:{goal:'s1',a:'s1'},
    snapshot_history:[{snapshot:{snapshot_id:'s1'},origin:'user-imported',observed_at:'2026-11-08T03:00:00Z'}],
    snapshot_errors:[], snapshot:{goal:{objective:'Goal',constraints:['Read only'],questions:[]},
      subtasks:[{id:'a',title:'A',progress:'done',dependencies:[]}],claims:[{scope:'a',verification:{status:'unknown'}}]}};
  const result = snapshotModel(view,'a',Date.parse('2026-11-08T03:01:00Z'));
  assert.deepEqual(result.constraints,['Read only']);
  assert.equal(result.tasks[0].progress,'done');
  assert.equal(result.tasks[0].verification,'Unknown; no independent verification');
  assert.equal(result.age,'1 minute ago');
  assert.equal(result.origin,'user-imported');
  view.activity_at='2100-01-01';
  assert.equal(snapshotModel(view,'a',Date.parse('2026-11-08T03:01:00Z')).age,'1 minute ago');
  assert.equal(snapshotModel(view,'removed').scope,'goal');
});

test('empty and invalid snapshot presentation retains errors and import origin', async () => {
  const { snapshotModel, snapshotRequest } = await import('../../web/model.mjs');
  const result=snapshotModel({snapshot:null,snapshot_errors:[{error:'Invalid newer snapshot'}]},'goal');
  assert.equal(result.empty,true);
  assert.equal(result.errors[0].error,'Invalid newer snapshot');
  assert.match(snapshotRequest({schema_version:1}),/context-viewer-snapshot/);
  assert.match(snapshotRequest({schema_version:1}),/supersedes/);
});

test('corrections retain context, scope, claim and original source evidence', async () => {
  const { correctionText, scopedSources } = await import('../../web/model.mjs');
  const source={id:'src',title:'guide',scopes:['a'],matches:[{key:'key',text:'<img> original',status:'recorded-supplied'}]};
  const view={id:'ctx',snapshot:{goal_id:'g'},sources:[source,{id:null,scopes:[]}]};
  assert.equal(scopedSources(view,'goal').length,0);
  assert.equal(scopedSources(view,'a').length,1);
  const text=correctionText(view,'a',{id:'c',statement:'Disputed'},source);
  for (const expected of ['ctx','g','a','c','src','Disputed','<img> original','requested check','Acknowledgment']) assert.ok(text.includes(expected),expected);
});

test('copy uses edited text and selects fallback without implying delivery', async () => {
  const { copyEditable } = await import('../../web/model.mjs');
  let copied;const editor={value:'User edited request',focus(){this.focused=true;},select(){this.selected=true;}};
  assert.match(await copyEditable(editor,{writeText:async text=>{copied=text;}}),/Copied/);
  assert.equal(copied,'User edited request');
  assert.match(await copyEditable(editor,{writeText:async()=>{throw Error('denied');}}),/manually/);
  assert.equal(editor.selected,true);assert.equal(editor.focused,true);
});


test('project grouping preserves recency, exact paths, contexts and unknown projects', async () => {
  const { sessionGroups } = await import('../../web/model.mjs');
  const sessions = [
    { id: 'new', project: '/repo/a', contexts: [{id:'new:0'}, {id:'new:1'}] },
    { id: 'other', project: '/repo/b' },
    { id: 'old', project: '/repo/a' },
    { id: 'nested', project: '/repo/a/subdir' },
    { id: 'unknown', project: '' },
    { id: 'missing' },
  ];
  const groups = sessionGroups(sessions);
  assert.deepEqual(groups.map(g => g.project), ['/repo/a', '/repo/b', '/repo/a/subdir', '']);
  assert.deepEqual(groups.map(g => g.sessions.map(s => s.id)),
    [['new', 'old'], ['other'], ['nested'], ['unknown', 'missing']]);
  assert.equal(groups[0].sessions[0], sessions[0]);
  assert.deepEqual(groups[0].sessions[0].contexts, [{id:'new:0'}, {id:'new:1'}]);
  assert.deepEqual(sessionGroups([]), []);
});

test('file head keeps report age when the current report is off the history page', async () => {
  const { snapshotModel } = await import('../../web/model.mjs');
  const view={snapshot:{goal:{objective:'Current',constraints:[],questions:[]},subtasks:[],claims:[]},snapshot_heads:{goal:'s129'},snapshot_history:[],
    current_report:{snapshot:{snapshot_id:'s129'},observed_at:'2026-11-08T03:00:00Z',origin:'file agent self-report'}};
  assert.equal(snapshotModel(view,'goal',Date.parse('2026-11-08T03:01:00Z')).age,'1 minute ago');
});

test('file grouping uses explicit task references without merging sessions', async () => {
  const { taskGroups } = await import('../../web/model.mjs');
  const a={history_id:'a',task_ref:'/project/progress.md',session_id:'one'};
  const b={history_id:'b',task_ref:'/project/progress.md',session_id:'two'};
  const c={history_id:'c',task_ref:null,session_id:'one'};
  assert.deepEqual(taskGroups([a,b,c]).map(g=>[g.task,g.histories.map(h=>h.history_id)]),
    [['/project/progress.md',['a','b']],[null,['c']]]);
});

test('calendar accordions group local days newest first and retain unknown dates', async () => {
  const { dateGroups } = await import('../../web/model.mjs');
  const today = new Date(2026, 10, 9, 12);
  const items = [{id:'old',at:new Date(2026,10,8,23,59)}, {id:'today',at:new Date(2026,10,9,0,1)}, {id:'unknown',at:null}, {id:'invalid',at:'bad'}];
  const groups = dateGroups(items, item => item.at, today);
  assert.deepEqual(groups.map(g=>[g.key,g.today,g.items.map(i=>i.id)]), [
    ['2026-11-09',true,['today']], ['2026-11-08',false,['old']], ['unknown',false,['unknown','invalid']]]);
});

test('new-tab source text preserves provenance, bounds and literal captured content', async () => {
  const { capturedSourceText } = await import('../../web/model.mjs');
  const text = capturedSourceText({id:'ctx'}, {title:'guide',origin:'snapshot-reported reference', matches:[{
    key:'r1',status:'recorded-read',observed_at:'2026-11-09',coverage:'excerpt',version:'Unknown',display_note:'Display excerpt limited',text:'<script>hostile</script>'}]});
  for (const value of ['ctx','guide','Historical captured evidence','not the current source file','excerpt','Display excerpt limited','<script>hostile</script>']) assert.ok(text.includes(value));
  assert.match(capturedSourceText({id:'ctx'}, {title:'missing',origin:'report',matches:[]}), /coverage unknown/);
});
