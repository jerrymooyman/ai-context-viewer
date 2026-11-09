import json
from pathlib import Path
import tempfile
import unittest
from viewer.contexts import ContextStore
from viewer.logs import discover_sessions
from test_snapshots import sample


class SnapshotContextTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name); self.path=self.root/'sample.jsonl'
        self.path.write_text(json.dumps(dict(type='session_meta',payload=dict(id='a',cli_version='0.167.0')))+'\n')
        self.summary=discover_sessions(self.root)[0]
        self.store=ContextStore(self.root,self.root/'state.json')

    def append(self,payload,role='assistant',timestamp='2026-11-08T03:00:00Z'):
        message='```context-viewer-snapshot\n'+json.dumps(payload)+'\n```'
        with self.path.open('a') as stream:
            stream.write(json.dumps(dict(type='response_item',timestamp=timestamp,payload=dict(type='message',role=role,content=[dict(text=message)])))+'\n')

    def test_log_timestamp_and_invalid_newer_retention(self):
        self.append(sample(),role='user')
        self.assertIsNone(self.store.view(self.summary,self.summary.id)['snapshot'])
        self.append(sample())
        view=self.store.view(self.summary,self.summary.id)
        self.assertEqual(view['snapshot_at'],'2026-11-08T03:00:00Z')
        self.assertEqual(view['snapshot_history'][0]['origin'],'agent-self-report')
        invalid=sample('s2',supersedes='s1');invalid['schema_version']=99
        self.append(invalid,timestamp='2026-11-08T04:00:00Z')
        view=self.store.view(self.summary,self.summary.id)
        self.assertEqual(view['snapshot_at'],'2026-11-08T03:00:00Z')
        self.assertEqual(view['activity_at'],'2026-11-08T04:00:00Z')
        self.assertEqual(len(view['snapshot_errors']),1)
        self.assertEqual(len(view['snapshot_history']),1)

    def test_manual_import_receipt_and_restart_loss(self):
        before=self.path.read_bytes()
        result=self.store.import_snapshot(self.summary,self.summary.id,json.dumps(sample()))
        self.assertTrue(result.valid)
        view=self.store.view(self.summary,self.summary.id)
        self.assertEqual(view['snapshot_history'][0]['origin'],'user-imported')
        self.assertIsNotNone(view['snapshot_at'])
        self.assertIsNone(view['snapshot_history'][0]['evidence_key'])
        self.assertIsNone(ContextStore(self.root).view(self.summary,self.summary.id)['snapshot'])
        self.assertEqual(self.path.read_bytes(),before)
        result=self.store.import_snapshot(self.summary,self.summary.id,'{"x":1,"x":2}')
        self.assertFalse(result.valid)
        self.assertEqual(len(self.store.view(self.summary,self.summary.id)['snapshot_errors']),1)

    def test_manual_context_needs_no_log_and_unsupported_can_import(self):
        summary=self.store.create_import_context()
        self.assertTrue(self.store.import_snapshot(summary,summary.id,json.dumps(sample())).valid)
        view=self.store.view(summary,summary.id)
        self.assertEqual(view['status'],'user-imported')
        self.assertEqual(view['supplied'],[])
        self.assertTrue(any('unknown' in w.lower() for w in view['warnings']))
        self.summary.status='unsupported'
        self.assertTrue(self.store.import_snapshot(self.summary,self.summary.id,json.dumps(sample())).valid)
        self.assertIsNotNone(self.store.view(self.summary,self.summary.id)['snapshot'])

    def test_boundary_does_not_carry_snapshot_or_import_forward(self):
        self.append(sample())
        view=self.store.view(self.summary,self.summary.id)
        self.store.import_snapshot(self.summary,self.summary.id,json.dumps(sample('s2',supersedes='s1')))
        with self.path.open('a') as stream:
            stream.write(json.dumps(dict(type='event_msg',payload=dict(type='handoff')))+'\n')
        view=self.store.view(self.summary,self.summary.id)
        second=self.store.declare(self.summary,self.summary.id,view['activity'][-1]['key'])
        self.assertIsNone(self.store.view(self.summary,second)['snapshot'])
        self.assertEqual(len(self.store.view(self.summary,self.summary.id)['snapshot_history']),2)

    def test_import_then_log_update_uses_ingestion_order_and_survives_log_switch(self):
        self.append(sample())
        self.store.view(self.summary,self.summary.id)
        self.assertTrue(self.store.import_snapshot(self.summary,self.summary.id,json.dumps(sample('s2','a','s1'))).valid)
        self.append(sample('s3','a','s2'))
        view=self.store.view(self.summary,self.summary.id)
        self.assertEqual(view['snapshot_heads']['a'],'s3')
        self.assertEqual([e['snapshot']['snapshot_id'] for e in view['snapshot_history']],['s1','s2','s3'])
        other=self.root/'other.jsonl';other.write_text(self.path.read_text().splitlines()[0]+'\n')
        other_summary=next(s for s in discover_sessions(self.root) if s.path==other)
        self.store.view(other_summary,other_summary.id)
        view=self.store.view(self.summary,self.summary.id)
        self.assertEqual(len(view['snapshot_history']),3)
        self.assertEqual(view['snapshot_errors'],[])

    def test_replacement_invalidates_snapshot_and_import(self):
        self.append(sample());self.store.view(self.summary,self.summary.id)
        self.store.import_snapshot(self.summary,self.summary.id,json.dumps(sample('s2',supersedes='s1')))
        self.path.write_text(self.path.read_text().splitlines()[0]+'\n')
        view=self.store.view(self.summary,self.summary.id)
        self.assertIsNone(view['snapshot'])
        self.assertEqual(view['snapshot_history'],[])
