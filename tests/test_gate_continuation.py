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


if __name__ == '__main__': unittest.main()
