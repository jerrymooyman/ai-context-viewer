from copy import deepcopy
import unittest
from viewer.snapshots import validate_snapshot, apply_snapshot, parse_snapshot, SnapshotHistory


def sample(snapshot_id='s1', scope='goal', supersedes=None):
    return dict(schema_version=1, snapshot_id=snapshot_id, goal_id='g1', scope=scope, supersedes=supersedes,
        goal=dict(objective='Inspect working assumptions', constraints=['Read only'], questions=['Coverage?']) if scope == 'goal' else None,
        subtasks=[dict(id=s, title=s, dependencies=[] if s == 'a' else ['a'], progress='planned')
                  for s in (['a', 'b'] if scope == 'goal' else [scope])],
        claims=[dict(id='claim-' + scope, scope=scope, statement='An assumption', kind='assumption', source_ids=['source1'],
                     verification=dict(status='unknown', evidence_keys=[]))],
        sources=[dict(id='source1', title='Reported source', evidence_keys=[])])


class SnapshotTests(unittest.TestCase):
    def test_goal_and_subtask_update_preserve_unrelated_scope(self):
        h = SnapshotHistory()
        first = sample()
        first['claims'].append(dict(first['claims'][0], id='claim-b', scope='b'))
        self.assertTrue(apply_snapshot(h, first, 'context-a').valid)
        update = sample('s2', 'a', 's1')
        update['subtasks'][0]['progress'] = 'done'
        self.assertTrue(apply_snapshot(h, update, 'context-a').valid)
        self.assertEqual(h.current['goal']['constraints'], ['Read only'])
        self.assertEqual(h.current['subtasks'][0]['progress'], 'done')
        self.assertEqual(h.current['subtasks'][1]['progress'], 'planned')
        self.assertIn('claim-b', [c['id'] for c in h.current['claims']])
        self.assertEqual(len(h.history), 2)

    def test_reject_invalid_ids_types_cycles_and_versions(self):
        variants=[]
        for key, value in [('snapshot_id',''), ('schema_version',2), ('subtasks',{}), ('goal_id',None)]:
            p=sample(); p[key]=value; variants.append(p)
        p=sample(); p['subtasks'][0]['dependencies']=['b']; variants.append(p)
        p=sample(); p['subtasks'][1]['id']='a'; variants.append(p)
        p=sample(); p['claims'][0]['scope']='missing'; variants.append(p)
        p=sample(); p['claims'][0]['verification']['status']='verified'; variants.append(p)
        for payload in variants:
            with self.subTest(payload=payload):
                self.assertFalse(validate_snapshot(payload).valid)

    def test_duplicate_keys_and_oversized_json_rejected(self):
        for text in ['{"schema_version":1,"schema_version":1}', 'x'*65537, '{"x":NaN}']:
            with self.assertRaises(ValueError): parse_snapshot(text)

    def test_invalid_newer_keeps_last_valid_and_cross_context_supersession_fails(self):
        h=SnapshotHistory()
        self.assertTrue(apply_snapshot(h,sample(),'a').valid)
        before=deepcopy(h.current)
        invalid=sample('s2'); invalid['schema_version']=9
        self.assertFalse(apply_snapshot(h,invalid,'a').valid)
        self.assertFalse(apply_snapshot(h,sample('s2','a','s1'),'b').valid)
        self.assertFalse(apply_snapshot(h,sample('s2','a','missing'),'a').valid)
        self.assertEqual(h.current,before)
        self.assertEqual(len(h.history),1)

    def test_goal_replacement_removes_old_subtasks_and_claims(self):
        h=SnapshotHistory(); apply_snapshot(h,sample(),'a')
        p=sample('s2',supersedes='s1'); p['subtasks']=[]; p['claims']=[]; p['sources']=[]
        self.assertTrue(apply_snapshot(h,p,'a').valid)
        self.assertEqual(h.current['subtasks'],[])
        self.assertEqual(h.current['claims'],[])
        self.assertEqual(len(h.history),2)

    def test_duplicate_snapshot_id_and_stale_ancestry_rejected(self):
        h=SnapshotHistory(); apply_snapshot(h,sample(),'a')
        self.assertFalse(apply_snapshot(h,sample(),'a').valid)
        self.assertTrue(apply_snapshot(h,sample('s2','a','s1'),'a').valid)
        self.assertFalse(apply_snapshot(h,sample('s3','a','s1'),'a').valid)

    def test_subtask_dependency_cycle_against_other_scope_rejected(self):
        h=SnapshotHistory(); apply_snapshot(h,sample(),'a')
        update=sample('s2','a','s1');update['subtasks'][0]['dependencies']=['b']
        self.assertFalse(apply_snapshot(h,update,'a').valid)
        self.assertEqual(h.current['subtasks'][0]['dependencies'],[])

    def test_source_and_claim_conflicts_cannot_change_other_scopes(self):
        h=SnapshotHistory(); apply_snapshot(h,sample(),'a')
        update=sample('s2','a','s1');update['sources'][0]['title']='Different source'
        self.assertFalse(apply_snapshot(h,update,'a').valid)
        update=sample('s2','a','s1');update['claims'][0]['id']='claim-goal'
        self.assertFalse(apply_snapshot(h,update,'a').valid)

    def test_missing_required_fields_unknown_dependencies_and_noninteger_version(self):
        for field in sample():
            payload=sample(); del payload[field]
            self.assertFalse(validate_snapshot(payload).valid)
        p=sample();p['subtasks'][0]['dependencies']=['missing']
        self.assertFalse(validate_snapshot(p).valid)
        p=sample();p['schema_version']=True
        self.assertFalse(validate_snapshot(p).valid)

    def test_published_example_and_fixture_sequence(self):
        from pathlib import Path
        fixtures=Path(__file__).parent/'fixtures'/'snapshots'
        example=parse_snapshot((Path(__file__).parent.parent/'web'/'snapshot-example.json').read_text())
        goal=parse_snapshot((fixtures/'goal.json').read_text())
        self.assertEqual(goal,example)
        h=SnapshotHistory()
        self.assertTrue(apply_snapshot(h,goal,'fixture-context').valid)
        self.assertTrue(apply_snapshot(h,parse_snapshot((fixtures/'subtask-update.json').read_text()),'fixture-context').valid)
        self.assertFalse(apply_snapshot(h,parse_snapshot((fixtures/'invalid-version.json').read_text()),'fixture-context').valid)
        self.assertEqual(h.current['subtasks'][0]['progress'],'in_progress')
