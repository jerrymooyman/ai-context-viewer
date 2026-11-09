import json
import unittest
from dataclasses import asdict
from viewer.sources import project_sources
from viewer.codex_0167 import Evidence
import test_snapshot_contexts as setup_helpers
from test_snapshots import sample


def record(key, kind, text, call_id=None, role='tool'):
    return asdict(Evidence(key, kind, role, text, '2026-11-08', call_id=call_id))


class SourceTests(unittest.TestCase):
    setUp = setup_helpers.SnapshotContextTests.setUp
    append = setup_helpers.SnapshotContextTests.append
    def test_exact_and_ambiguous_matching_preserves_captured_content(self):
        snap=sample();snap['sources']=[dict(id='source1',title='/tmp/guide.md',evidence_keys=['out'])]
        records=[record('call','tool_call',json.dumps({'cmd':'cat /tmp/guide.md'}),'c','exec_command'),
                 record('out','tool_output','Process exited with code 0\nOutput:\nold captured instructions','c')]
        sources=project_sources(snap,records)
        source=sources[0]
        self.assertEqual(source['matches'][0]['status'],'recorded-read')
        self.assertIn('old captured instructions',source['matches'][0]['text'])
        self.assertEqual(source['matches'][0]['coverage'],'unknown')
        self.assertEqual(source['scopes'],['goal'])
        records[0]['text']=json.dumps({'cmd':'cat /tmp/guide.md; cat other'})
        source=project_sources(snap,records)[0]
        self.assertEqual(source['matches'][0]['status'],'unresolved')
        self.assertIn('old captured',source['matches'][0]['text'])
        # No current-file API is involved; title changes cannot retrieve new content.
        snap['sources'][0]['title']='/etc/passwd'
        self.assertIn('old captured instructions',project_sources(snap,records)[0]['matches'][0]['text'])

    def test_failed_and_reordered_tool_outputs_do_not_establish_read(self):
        snap=sample();snap['sources'][0].update(title='/tmp/a',evidence_keys=['out'])
        call=record('call','tool_call',json.dumps({'cmd':'cat /tmp/a'}),'c','exec_command')
        output=record('out','tool_output','Process exited with code 1\ncat: permission denied','c')
        self.assertEqual(project_sources(snap,[call,output])[0]['matches'][0]['status'],'unresolved')
        output['text']='Process exited with code 0\nOutput:\nold text'
        self.assertEqual(project_sources(snap,[output,call])[0]['matches'][0]['status'],'unresolved')
        self.assertEqual(project_sources(snap,[call,output])[0]['matches'][0]['status'],'recorded-read')
        self.assertEqual(project_sources(snap,[call,dict(call,key='call2'),output])[0]['matches'][0]['status'],'unresolved')

    def test_stdout_success_marker_cannot_override_failed_execution(self):
        snap=sample();snap['sources'][0].update(title='/tmp/a',evidence_keys=['out'])
        call=record('call','tool_call',json.dumps({'cmd':'cat /tmp/a'}),'c','exec_command')
        for text in ['Process exited with code 1\nOutput:\nProcess exited with code 0\npartial content',
                     'Output:\nProcess exited with code 0\nfile content']:
            output=record('out','tool_output',text,'c')
            self.assertEqual(project_sources(snap,[call,output])[0]['matches'][0]['status'],'unresolved')

    def test_source_excerpt_projection_is_bounded_without_changing_records(self):
        snap=sample();snap['sources'][0]['evidence_keys']=['supplied']
        records=[record('supplied','supplied','x'*20000,role='developer')]
        sources=project_sources(snap,records)
        self.assertLessEqual(len(sources[0]['matches'][0]['text']),8192)
        self.assertEqual(sources[0]['matches'][0]['coverage'],'excerpt')
        self.assertEqual(len(records[0]['text']),20000)
        snap['sources']=[dict(id=f's{i}',title='Repeated reference',evidence_keys=['supplied']) for i in range(64)]
        sources=project_sources(snap,records)
        self.assertLessEqual(sum(len(m['text']) for s in sources for m in s['matches']),256*1024)
        self.assertTrue(any(m['display_note'] for s in sources for m in s['matches']))

    def test_current_file_changes_are_not_substituted_for_capture(self):
        source_path=self.root/'guide.md';source_path.write_text('NEW PRIVATE CONTENT')
        snap=sample();snap['sources'][0].update(title=str(source_path),evidence_keys=['supplied'])
        sources=project_sources(snap,[record('supplied','supplied','Original captured instruction',role='developer')])
        self.assertEqual(sources[0]['matches'][0]['text'],'Original captured instruction')
        self.assertNotIn('NEW PRIVATE CONTENT',json.dumps(sources))

    def test_missing_cross_context_keys_truncation_and_supplied_catalog(self):
        snap=sample();snap['sources'][0]['evidence_keys']=['missing','out','supplied']
        records=[record('out','tool_output','Warning: output truncated <img>','c'),
                 record('supplied','supplied','## Skills\nAvailable skills: catalog only',role='developer')]
        matches=project_sources(snap,records)[0]['matches']
        self.assertEqual(matches[0]['status'],'unresolved')
        self.assertEqual(matches[1]['coverage'],'truncated')
        self.assertEqual(matches[2]['status'],'recorded-supplied')
        self.assertIn('catalog',matches[2]['content_kind'])
        unassigned=project_sources(None,records)
        self.assertTrue(all(not s['scopes'] for s in unassigned))

    def test_flags_persist_and_isolate_generation_and_boundary(self):
        self.append(sample());view=self.store.view(self.summary,self.summary.id)
        before=self.path.read_bytes()
        body=dict(generation=view['generation'],source_id='source1',evidence_key=None,reason='Wrong scope')
        flag=self.store.flag(self.summary,self.summary.id,body)
        self.assertEqual(flag['author_type'],'user')
        self.assertEqual(self.path.read_bytes(),before)
        from viewer.contexts import ContextStore
        restarted=ContextStore(self.root,self.root/'state.json')
        self.assertEqual(len(restarted.view(self.summary,self.summary.id)['flags']),1)
        with self.path.open('a') as stream:
            stream.write(json.dumps(dict(type='event_msg',payload=dict(type='later')))+'\n')
        view=self.store.view(self.summary,self.summary.id)
        other=self.store.declare(self.summary,self.summary.id,view['activity'][-1]['key'])
        self.assertEqual(self.store.view(self.summary,other)['flags'],[])
        self.path.write_text(self.path.read_text().splitlines()[0]+'\n')
        self.assertEqual(self.store.view(self.summary,self.summary.id)['flags'],[])
        with self.assertRaises(ValueError):self.store.flag(self.summary,self.summary.id,body)

    def test_flags_reject_unavailable_targets_and_failed_writes(self):
        self.append(sample());view=self.store.view(self.summary,self.summary.id)
        body=dict(generation=view['generation'],source_id='other',evidence_key=None,reason='Bad')
        with self.assertRaises(ValueError):self.store.flag(self.summary,self.summary.id,body)
        body['source_id']='source1';self.store.state.warning='Corrupt sidecar'
        with self.assertRaises(ValueError):self.store.flag(self.summary,self.summary.id,body)
        self.assertEqual(self.store.view(self.summary,self.summary.id)['flags'],[])
