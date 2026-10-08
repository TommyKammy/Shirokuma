from contextlib import ExitStack
from pathlib import Path
import tempfile
import unittest
from unittest import mock
from tools.local_experiment import gate_continuation as C


def report():
    return dict(schema_version=1, topic='doctor', approach='diagnose',
                progress='measured_blocker', next_topic='source_identity',
                next_approach='diagnose', next_target='Inspect the pinned source identity against current owned lab state.')


class ContinuationTests(unittest.TestCase):
    def test_proposals_cannot_expand_scope_or_execute_commands(self):
        self.assertEqual(C.proposal(report()), report())
        for bad in (dict(report(), next_topic='unrelated_project'),
                    dict(report(), shell='rm'), dict(report(), schema_version=True),
                    dict(report(), next_target='short'), dict(report(), next_approach='unsuspend')):
            with self.assertRaises(C.R.RunnerError): C.proposal(bad)

    def test_counter_persists_across_series(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for n in range(3):
                d = root/'tmp/local-experiment/gate-continuation'/f'series-{n}'
                d.mkdir(parents=True)
                if n < 2: C.reserve(root, 'doctor', 'diagnose', d, 1)
                else:
                    with self.assertRaisesRegex(C.R.RunnerError, 'CONTINUATION_APPROACH_EXHAUSTED'):
                        C.reserve(root, 'doctor', 'diagnose', d, 1)

    def test_milestone_does_not_stop_next_mission(self):
        with tempfile.TemporaryDirectory() as tmp, ExitStack() as stack:
            root = Path(tmp)
            parent = root/'tmp/local-experiment/gate-supervisor'; parent.mkdir(parents=True)
            (parent/'lock').touch()
            # Real first-run birthtime is available on this macOS harness.
            (root/'tmp/local-experiment/autonomous-runner'/C.R.FIRST_RUN).mkdir(parents=True)
            stack.enter_context(mock.patch.object(C.R, 'checkout_head', side_effect=['a'*40,'b'*40,'b'*40,'c'*40]))
            stack.enter_context(mock.patch.object(C.R, 'subscription_preflight'))
            stack.enter_context(mock.patch.object(C.R, 'gate_preflight'))
            stack.enter_context(mock.patch.object(C.S, 'no_active_worker'))
            stack.enter_context(mock.patch.object(C.S, 'terminal'))
            stack.enter_context(mock.patch.object(C.S, 'probe', return_value={'verified': True}))
            stack.enter_context(mock.patch.object(C.S, 'command'))
            stack.enter_context(mock.patch.object(C, 'doctor'))
            stack.enter_context(mock.patch.object(C, 'progress'))
            r1=report(); r2=dict(r1, topic='source_identity', next_topic='local_admission')
            # Report reads only; counter reservation files are not read in this mocked test.
            stack.enter_context(mock.patch.object(C, 'reserve'))
            stack.enter_context(mock.patch.object(C.R, 'private_json', side_effect=[r1,r2]))
            worker = stack.enter_context(mock.patch.object(C.R, 'run', return_value=('run_budget_reached', root/'gate-fake')))
            C.run(root, root/'vault', 2, 120)
            self.assertEqual(worker.call_count, 2)
            self.assertIn('doctor/diagnose', worker.call_args_list[0].kwargs['mission']['target_evidence'])
            self.assertIn('source_identity/diagnose', worker.call_args_list[1].kwargs['mission']['target_evidence'])

    def test_no_progress_cannot_repeat_same_approach(self):
        r = dict(report(), progress='no_progress', next_topic='doctor')
        with mock.patch.object(C.R, 'git', return_value=''):
            with self.assertRaisesRegex(C.R.RunnerError, 'NO_PROGRESS_SAME_APPROACH'):
                C.progress(Path('/unused'), 'a'*40, 'b'*40, r, Path('/unused'), 1)

    def test_plan_has_zero_model_calls(self):
        with mock.patch('sys.argv', ['continuation']), mock.patch.object(C, 'run') as run:
            self.assertEqual(C.main(), 0)
            run.assert_not_called()

    def test_independent_findings_keeps_old_counters_and_no_safe_stop(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); d=root/'review'; d.mkdir()
            (root/'tmp/local-experiment/gate-batches').mkdir(parents=True)
            worker=root/'tmp/local-experiment/autonomous-runner/gate-held'
            (worker/'attempt-001').mkdir(parents=True)
            C.R.save_json(worker/'attempt-001/checkpoint.json', {'reason':'no_safe_work'})
            state={'reason':'agent_no_safe_work_claim'}; result={'reason':'no_safe_work'}
            r=dict(report(),topic='local_admission',approach='repository_repair',
                   next_target='Diagnose retained Flux HIGH findings and evidence freshness without approval.')
            with self.assertRaisesRegex(C.R.RunnerError,'EXHAUSTED_SCOPE_UNVERIFIED'):
                C.independent_findings_review(root,worker,r,state,result,'ses_fixture','a'*40,d,{})
            for n in range(2):
                p=root/'tmp/local-experiment/gate-continuation'/f'series-{n}'
                p.mkdir(parents=True)
                C.R.save_json(p/'reservation-001.json',dict(topic='local_admission',approach='repository_repair'))
            nxt=C.independent_findings_review(root,worker,r,state,result,'ses_fixture','a'*40,d,{'verified':True})
            self.assertEqual(nxt['next_topic'],'flux_findings')
            self.assertEqual(len(list((root/'tmp/local-experiment/gate-continuation').glob('series-*/reservation-*.json'))),2)
            review=C.R.private_json(root/'tmp/local-experiment/gate-batches/independent-review-gate-held.json','TEST')
            self.assertEqual(review['held_scope'],'local_admission/repository_repair')
            self.assertEqual(review['allowed_gate'],C.FINDINGS_GATE)

    def test_finding_mission_never_approves_or_activates(self):
        v=C.objective('a'*40,'flux_findings','diagnose','Analyze exact retained HIGH finding IDs and evidence freshness without approval.','private/proposal.json')
        self.assertEqual(v['gate_id'],C.FINDINGS_GATE)
        self.assertIn('no approvals',v['stop_conditions'])
        self.assertIn('riskDecision change',v['stop_conditions'])
        self.assertIn('Next proposal stays flux_findings',v['target_evidence'])

    def test_completed_findings_no_safe_work_is_not_admission_pivot(self):
        with tempfile.TemporaryDirectory() as tmp:
            d=Path(tmp)
            r=dict(report(),topic='flux_findings',approach='diagnose',next_topic='flux_findings')
            with self.assertRaisesRegex(C.R.RunnerError,'FINDINGS_REFRESH_CAPABILITY_REQUIRED'):
                C.independent_findings_review(d,d/'gate-fixture',r,
                    {'reason':'agent_no_safe_work_claim'},{'reason':'no_safe_work'},'ses_fixture','a'*40,d,{})
            self.assertTrue((d/'findings-held-gate-fixture.json').exists())


if __name__ == '__main__': unittest.main()
