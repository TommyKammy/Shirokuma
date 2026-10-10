from pathlib import Path
import tempfile
import unittest
from unittest import mock

from tools.local_experiment import restart_supervisor as S


class RestartTests(unittest.TestCase):
    def test_kustomize_os_layer_default_never_launches(self):
        with mock.patch('sys.argv',['restart','--kustomize-os-layer']),mock.patch.object(S,'test_planning') as run:
            self.assertEqual(S.main(),0);run.assert_not_called()

    def test_kustomize_os_default_never_launches(self):
        with mock.patch('sys.argv',['restart','--kustomize-os-repair']),mock.patch.object(S,'test_planning') as run:
            self.assertEqual(S.main(),0);run.assert_not_called()

    def test_unknown_content_binds_bytes_not_allocated_block_changes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);scope=root/'tmp/rv/mis';scope.mkdir(parents=True)
            (scope/'data').write_bytes(b'held content')
            first=S.unknown_content(root)
            (scope/'data').write_bytes(b'other content')
            self.assertNotEqual(S.unknown_content(root),first)
            (scope/'link').symlink_to(scope/'data')
            with self.assertRaises(S.R.RunnerError):S.unknown_content(root)

    def test_independent_kustomize_default_never_launches(self):
        with mock.patch('sys.argv',['restart','--kustomize-repair']),mock.patch.object(S,'test_planning') as run:
            self.assertEqual(S.main(),0);run.assert_not_called()

    def test_deadline_extension_only_exact_original_series(self):
        old=1791588714.0429842
        with mock.patch.object(Path,'stat',return_value=mock.Mock(st_birthtime=old-S.R.ORIGINAL_WEEK_MINUTES*60)):
            self.assertEqual(S.R.execution_deadline(Path('/fixture')),1791682200.0)
        with mock.patch.object(Path,'stat',return_value=mock.Mock(st_birthtime=100)):
            self.assertEqual(S.R.execution_deadline(Path('/fixture')),100+S.R.ORIGINAL_WEEK_MINUTES*60)
        with mock.patch.object(S.R,'execution_deadline',return_value=1791682200.0), \
             mock.patch.object(S.S.time,'time',return_value=1791682200.0):
            with self.assertRaisesRegex(S.R.RunnerError,'ORIGINAL_WEEK_EXPIRED'):
                S.S.check_resume_budget(Path('/fixture'),1,1)
        self.assertEqual(S.R.SERIES_LIMIT,600)

    def test_minimal_repair_default_never_launches(self):
        with mock.patch('sys.argv',['restart','--minimal-tests']),mock.patch.object(S,'test_planning') as run:
            self.assertEqual(S.main(),0);run.assert_not_called()

    def test_test_planning_default_is_zero_calls(self):
        with mock.patch('sys.argv',['restart','--test-plan']),mock.patch.object(S,'test_planning') as run:
            self.assertEqual(S.main(),0);run.assert_not_called()

    def test_test_planning_exhausted_reservations_inhibit_launch(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);parent=root/'tmp/local-experiment/test-capacity-plan'
            parent.mkdir(parents=True,mode=0o700)
            for n in range(2):(parent/f'plan-{n}').mkdir()
            lock=root/'tmp/local-experiment/gate-supervisor';lock.mkdir();(lock/'lock').touch()
            with mock.patch.object(S.R,'run') as run:
                with self.assertRaisesRegex(S.R.RunnerError,'TEST_PLAN_APPROACH_EXHAUSTED'):
                    S.test_planning(root,root/'vault')
                run.assert_not_called()

    def test_capacity_parent_records_test_count_and_interpreter(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            result=mock.Mock(returncode=0,stdout=b'',stderr=b'Ran 17 tests in 0.1s\nOK\n')
            with mock.patch.object(S.subprocess,'run',return_value=result) as run:
                S.capacity_tests(root,root)
                self.assertEqual(run.call_args.args[0][0],S.sys.executable)
            result.stderr=b'Ran 0 tests in 0s\nOK\n'
            other=root/'empty';other.mkdir()
            with mock.patch.object(S.subprocess,'run',return_value=result):
                with self.assertRaisesRegex(S.R.RunnerError,'CAPACITY_PARENT_TESTS_FAILED'):
                    S.capacity_tests(root,other)

    def test_resume_rejects_other_hold_without_worker_or_pilot(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);old=root/'tmp/local-experiment/safe-restart/restart-test'
            old.mkdir(parents=True,mode=0o700)
            S.R.save_json(old/'summary.json',dict(classification='HELD',operation_replay=False,reason='UNKNOWN'))
            with mock.patch.object(S.R,'run') as run,mock.patch.object(S,'api_pilot') as pilot:
                with self.assertRaisesRegex(S.R.RunnerError,'RESTART_RESUME_UNVERIFIED'):
                    S.resume_review(root,root/'vault',old)
                run.assert_not_called();pilot.assert_not_called()

    def test_default_does_not_start_workers_or_pilot(self):
        with mock.patch('sys.argv',['restart']),mock.patch.object(S,'run') as run:
            self.assertEqual(S.main(),0);run.assert_not_called()

    def test_proposal_cannot_supply_commands_or_runtime_authority(self):
        value=dict(schema_version=1,decision='ready_for_api_pilot',pilot_package='api',
                   notes='Capacity tests passed; old scopes held and source binary retries exhausted.')
        self.assertEqual(S.plan(value),value)
        for bad in (dict(value,command='start vm'),dict(value,pilot_package='controllers'),
                    dict(value,decision='deploy'),dict(value,schema_version=True)):
            with self.assertRaises(S.R.RunnerError):S.plan(bad)

    def test_parent_counts_allocation_and_rejects_links(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);p=root/'data';p.write_bytes(b'x');st=p.stat()
            self.assertEqual(S.measure(root),max(st.st_size,st.st_blocks*512))
            (root/'link').symlink_to(p)
            with self.assertRaises(S.R.RunnerError):S.measure(root)

    def test_exhausted_restart_reservations_never_launch(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);parent=root/'tmp/local-experiment/safe-restart';parent.mkdir(parents=True,mode=0o700)
            for n in range(2):(parent/f'restart-{n}').mkdir()
            lock=root/'tmp/local-experiment/gate-supervisor';lock.mkdir();(lock/'lock').touch()
            with mock.patch.object(S.R,'run') as run:
                with self.assertRaisesRegex(S.R.RunnerError,'RESTART_REPAIR_APPROACH_EXHAUSTED'):
                    S.run(root,root/'vault')
                run.assert_not_called()

    def test_mission_limits_and_old_scope_hold(self):
        value=S.mission('a'*40,S.R.ROOT/'tmp/local-experiment/safe-restart/restart-fixture')
        self.assertEqual(value['gate_id'],S.GATE)
        self.assertIn('third source binary attempt',value['stop_conditions'])
        self.assertIn('Repository-only',value['allowed_scope'])
        for key in ('baseline','target_evidence','allowed_scope','stop_conditions','recovery'):
            self.assertTrue(30<=len(value[key])<=650)

    def test_active_worker_prevents_model_and_pilot(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);lock=root/'tmp/local-experiment/gate-supervisor';lock.mkdir(parents=True);(lock/'lock').touch()
            with mock.patch.object(S.R,'checkout_head',return_value='a'*40), \
                 mock.patch.object(S.S,'no_active_worker',side_effect=S.R.RunnerError('ACTIVE_WORKER')), \
                 mock.patch.object(S.R,'run') as run,mock.patch.object(S,'api_pilot') as pilot:
                with self.assertRaisesRegex(S.R.RunnerError,'ACTIVE_WORKER'):S.run(root,root/'vault')
                run.assert_not_called();pilot.assert_not_called()

    def test_unknown_terminal_prevents_launch_and_preserves_hold(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);lock=root/'tmp/local-experiment/gate-supervisor';lock.mkdir(parents=True);(lock/'lock').touch()
            with mock.patch.object(S.R,'checkout_head',return_value='a'*40), \
                 mock.patch.object(S.S,'no_active_worker'),mock.patch.object(S.S,'check_resume_budget'), \
                 mock.patch.object(S.R,'subscription_preflight'),mock.patch.object(S.F,'stopped',return_value=[]), \
                 mock.patch.object(S,'retained',return_value={'security/resident-images.json':'a'*64}), \
                 mock.patch.object(S.S,'terminal',side_effect=S.R.RunnerError('SESSION_NOT_TERMINAL')), \
                 mock.patch.object(S.R,'run') as run,mock.patch.object(S,'api_pilot') as pilot:
                with self.assertRaisesRegex(S.R.RunnerError,'SESSION_NOT_TERMINAL'):S.run(root,root/'vault')
                run.assert_not_called();pilot.assert_not_called()
                files=list((root/'tmp/local-experiment/safe-restart').glob('restart-*/summary.json'))
                self.assertEqual(len(files),1)
                value=S.R.private_json(files[0],'TEST')
                self.assertEqual(value['classification'],'HELD')
                self.assertFalse(value['operation_replay'])

    def test_pilot_guard_terminates_only_owned_group_and_records_hold(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);evidence=mock.Mock(directory=root)
            evidence.environment.return_value={'PATH':'/unused'}
            capacity=mock.Mock();capacity.observe.side_effect=[{'bulk_writes_allowed':True},
                                                             {'bulk_writes_allowed':False}]
            process=mock.Mock(returncode=None);process.poll.return_value=None
            def terminate(p):p.returncode=-15
            with mock.patch.object(S.importlib,'import_module',return_value=capacity), \
                 mock.patch.object(S.subprocess,'Popen',return_value=process) as launch, \
                 mock.patch.object(S.R,'terminate',side_effect=terminate) as stop:
                with self.assertRaisesRegex(S.R.RunnerError,'API_PILOT_WRITE_GUARD'):
                    S.pilot_tests(evidence,Path('/unused/go'),root,{},60)
                self.assertTrue(launch.call_args.kwargs['start_new_session'])
                stop.assert_called_once_with(process)
            record=S.R.private_json(root/'api-unit-tests.finished.json','TEST')
            self.assertEqual(record['returncode'],-15)
            self.assertEqual(record['disposition'],'held_no_replay')

    def test_pilot_initial_guard_does_not_start_operation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);evidence=mock.Mock(directory=root)
            capacity=mock.Mock();capacity.observe.return_value={'bulk_writes_allowed':False}
            with mock.patch.object(S.importlib,'import_module',return_value=capacity), \
                 mock.patch.object(S.subprocess,'Popen') as launch:
                with self.assertRaisesRegex(S.R.RunnerError,'API_PILOT_WRITE_GUARD'):
                    S.pilot_tests(evidence,Path('/unused/go'),root,{},60)
                launch.assert_not_called()
            self.assertEqual(list(root.iterdir()),[])


if __name__=='__main__':unittest.main()
