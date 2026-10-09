import copy
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from tools.local_experiment import flux_test_plan as P


class FluxTestPlanTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.root.chmod(0o700)

    def fixture(self, retained=1000, seed=2000):
        def m(n):
            return dict(files=1, logical_bytes=n, charged_bytes=n,
                        copy_bytes=n, manifest_sha256='a'*64)
        measured = {b: m(retained) for b in P.BUILDS}
        measured['module_seed'] = m(seed)
        for key in P.MEASURED_KEYS-set(measured):
            measured[key] = m(0)
        outcomes = {k: dict(disposition='successful', tests_passed=1, packages_passed=1,
                            packages_started=[])
                    for k in ('old_root', 'old_signature', 'final_root', 'api')}
        outcomes['old_signature'].update(disposition='known_terminated', packages_passed=0)
        outcomes['final_root'].update(disposition='known_terminated')
        return P.propose(measured, 100*P.GIB, outcomes, 4096)

    def terminal(self, rc=0, disposition='succeeded', events=None):
        events = events or [dict(Action='pass', Test='TestA', Package='p'),
                            dict(Action='pass', Package='p')]
        data = b'\n'.join(json.dumps(e).encode() for e in events)
        return dict(returncode=rc, disposition=disposition, finished_at='dated',
                    stdout_sha256=hashlib.sha256(data).hexdigest()), data

    def test_ready_is_never_execution_authority(self):
        p = self.fixture()
        P.validate(p)
        self.assertEqual(p['decision'], 'ready_for_parent_review')
        self.assertIs(p['execution_authorized'], False)

    def test_retained_capacity_hold_does_not_mutate_inputs(self):
        p = self.fixture(retained=8*P.GIB-1)
        before = copy.deepcopy(p)
        P.validate(p)
        self.assertEqual(p, before)
        self.assertEqual(p['blockers'], ['combined_retention_budget'])

    def test_exact_hard_boundary_holds(self):
        self.assertIn('retained_hard_limit', self.fixture(retained=8*P.GIB)['blockers'])

    def test_exact_combined_boundary_holds(self):
        p = self.fixture()
        total = sum(u['total_bytes'] for u in p['units'])
        retained = (16*P.GIB-total)//2
        self.assertIn('combined_retention_budget', self.fixture(retained=retained)['blockers'])

    def test_free_space_subtracts_all_retained_new_units(self):
        p = self.fixture()
        p['free_bytes'] = 17*P.GIB + sum(u['total_bytes'] for u in p['units'])-1
        p['blockers'] = ['free_space_writer_margin']; p['decision'] = 'hold'
        P.validate(p)

    def test_oversized_module_copy_holds_not_cache_reuse(self):
        p = self.fixture(seed=7*P.GIB)
        self.assertIn('unit_bulk_limit', p['blockers'])

    def test_unknown_outcomes_hold(self):
        p = self.fixture()
        p['outcomes']['api']['disposition'] = 'unknown'
        p['blockers'] = ['unknown_operation_read_only_hold']; p['decision'] = 'hold'
        P.validate(p)

    def test_previously_started_final_index_cannot_be_proposed_as_new(self):
        p = self.fixture()
        p['outcomes']['final_root']['packages_started'] = [
            'github.com/fluxcd/source-controller/internal/index']
        with self.assertRaises(P.PlanError): P.validate(p)
        p['blockers'] = ['target_unit_previously_started_no_replay']; p['decision'] = 'hold'
        P.validate(p)

    def test_fixed_argv_scope_and_deadline_tampering_rejected(self):
        for field, value in (('argv', ['go', 'test', './...']), ('deadline_seconds', 601),
                             ('module_seed_bytes', 0), ('compile_cache_bytes', 0),
                             ('temp_bytes', 0), ('total_bytes', 1), ('id', 'signature')):
            with self.subTest(field=field):
                p = self.fixture(); p['units'][0][field] = value
                with self.assertRaises(P.PlanError): P.validate(p)

    def test_writable_paths_and_offline_environment_are_fixed(self):
        for key, value in (('GOCACHE', P.BUILDS[1]+'/gocache'),
                           ('TMPDIR', '/tmp'), ('GOPROXY', 'https://proxy.golang.org'),
                           ('GOWORK', 'auto')):
            p = self.fixture(); p['units'][0]['environment'][key] = value
            with self.assertRaises(P.PlanError): P.validate(p)

    def test_units_have_isolated_short_paths(self):
        a, b = self.fixture()['units']
        self.assertNotEqual(a['environment']['GOCACHE'], b['environment']['GOCACHE'])
        self.assertLess(len(a['environment']['TMPDIR']), 20)
        self.assertNotEqual(a['argv'][-2], b['argv'][-2])

    def test_false_ready_and_execution_flags_reject(self):
        p = self.fixture(retained=8*P.GIB-1); p['decision'] = 'ready_for_parent_review'
        with self.assertRaises(P.PlanError): P.validate(p)
        p = self.fixture(); p['execution_authorized'] = True
        with self.assertRaises(P.PlanError): P.validate(p)

    def test_extra_fields_missing_fields_duplicate_units_reject(self):
        for mutate in (lambda p: p.update(shell='go build'),
                       lambda p: p.pop('outcomes'),
                       lambda p: p['units'].__setitem__(1, copy.deepcopy(p['units'][0]))):
            p = self.fixture(); mutate(p)
            with self.assertRaises(P.PlanError): P.validate(p)

    def test_boolean_and_negative_numeric_evidence_reject(self):
        for n in (True, -1, 0.5):
            p = self.fixture(); p['measured']['module_seed']['copy_bytes'] = n
            with self.assertRaises(P.PlanError): P.validate(p)
        p = self.fixture(); p['schema_version'] = True
        with self.assertRaises(P.PlanError): P.validate(p)

    def test_receipt_success_requires_hash_exit_and_nonempty_tests(self):
        receipt, data = self.terminal()
        self.assertEqual(P.classify(receipt, data)['disposition'], 'successful')
        for change in ({'stdout_sha256': '0'*64}, {'returncode': True},
                       {'returncode': 1}, {'observation_error': 'failed'},
                       {'termination_error': 'unknown'}, {'finished_at': None}):
            altered = dict(receipt, **change)
            self.assertEqual(P.classify(altered, data)['disposition'], 'unknown')

    def test_zero_tests_or_package_only_success_is_unknown(self):
        r, d = self.terminal(events=[dict(Action='pass', Package='p')])
        self.assertEqual(P.classify(r, d)['disposition'], 'unknown')

    def test_known_terminated_passes_are_not_suite_success(self):
        r, d = self.terminal(-15, 'terminated_hold_no_replay')
        o = P.classify(r, d)
        self.assertEqual(o['disposition'], 'known_terminated')
        self.assertEqual(o['tests_passed'], 1)

    def test_missing_malformed_or_hash_mismatch_is_unknown_not_replayed(self):
        for receipt, data in (({}, b''), ({}, b'not json'),
                              ({'returncode': -15}, b'{}')):
            self.assertEqual(P.classify(receipt, data)['disposition'], 'unknown')

    def test_failure_event_cannot_be_success(self):
        r, d = self.terminal(events=[dict(Action='pass', Test='A'),
                                     dict(Action='pass'), dict(Action='fail')])
        self.assertEqual(P.classify(r, d)['disposition'], 'unknown')

    def test_measure_sparse_hardlinks_and_preserve_bytes_modes_mtime(self):
        scope = self.root/'scope'; scope.mkdir(mode=0o700)
        f = scope/'file'
        with f.open('wb') as stream: stream.truncate(10000)
        os.link(f, scope/'link')
        before = f.stat()
        m = P.measure(self.root, 'scope')
        self.assertEqual(m['files'], 2)
        self.assertEqual(m['charged_bytes'], 2*max(before.st_size, before.st_blocks*512))
        self.assertEqual(m['copy_bytes'], 4096+2*12288)
        self.assertEqual(f.stat().st_mtime_ns, before.st_mtime_ns)
        self.assertEqual(f.stat().st_mode, before.st_mode)

    def test_measure_symlink_and_denied_traversal_fail_closed(self):
        (self.root/'scope').mkdir(); (self.root/'scope/alias').symlink_to(self.root)
        with self.assertRaises(P.PlanError): P.measure(self.root, 'scope')
        with mock.patch.object(P.os, 'scandir', side_effect=PermissionError):
            with self.assertRaises(PermissionError): P.measure(self.root, 'scope')

    def test_read_ancestor_symlink_traversal_large_and_fifo_reject(self):
        (self.root/'scope').mkdir(); (self.root/'scope/f').write_bytes(b'ab')
        (self.root/'alias').symlink_to(self.root/'scope')
        for path in ('alias/f', '../scope/f', '/scope/f'):
            with self.assertRaises((OSError, P.PlanError)): P.read_bytes(self.root, path)
        with self.assertRaises(P.PlanError): P.read_bytes(self.root, 'scope/f', limit=1)
        os.mkfifo(self.root/'scope/fifo')
        with self.assertRaises(P.PlanError): P.read_bytes(self.root, 'scope/fifo')

    def test_measure_owner_mismatch_rejects(self):
        (self.root/'scope').mkdir()
        with mock.patch.object(P.os, 'getuid', return_value=os.getuid()+1):
            with self.assertRaises(P.PlanError): P.measure(self.root, 'scope')

    def test_planner_does_not_offer_execution_api(self):
        self.assertFalse(hasattr(P, 'subprocess'))
        self.assertFalse(hasattr(P, 'execute'))
        with self.assertRaises(P.PlanError): P.fixed_unit('root-all', 4096, 0)

    def test_inspect_binds_source_and_classifies_missing_receipt_unknown(self):
        for b in P.BUILDS:
            (self.root/b).mkdir(parents=True)
        for path in (P.BUILDS[1]+'/gomodcache', P.BUILDS[1]+'/gocache',
                     P.BUILDS[1]+'/temp', 'tmp/rv/api-w8dk1t4k/gocache',
                     'tmp/rv/api-w8dk1t4k/temp'):
            (self.root/path).mkdir(parents=True)
        for p in P.INPUTS:
            file = self.root/P.SOURCE/p
            file.parent.mkdir(parents=True, exist_ok=True)
            file.write_bytes(b'retained fixture')
        before = sorted(str(p.relative_to(self.root)) for p in self.root.rglob('*'))
        plan, hashes = P.inspect(self.root)
        self.assertEqual(plan['decision'], 'hold')
        self.assertIn('unknown_operation_read_only_hold', plan['blockers'])
        self.assertEqual(set(hashes), set(P.INPUTS))
        self.assertEqual(hashes['go.mod'], hashlib.sha256(b'retained fixture').hexdigest())
        self.assertEqual(before, sorted(str(p.relative_to(self.root)) for p in self.root.rglob('*')))
