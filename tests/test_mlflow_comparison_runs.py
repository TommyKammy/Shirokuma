"""Saved comparison discovery with private fakes, no fit, API or processes."""
from copy import deepcopy
import hashlib
import io
import json
import unittest
from unittest.mock import patch

from tools.local_experiment.mlflow import comparison_runs as R, comparison_predict as P
from tests.test_notebook_lab_comparison_saved import make_saved, COMPARISON_TOKEN


class ComparisonRunsTests(unittest.TestCase):
    def setUp(self):
        self.fixture = make_saved(self)
        for target, name in ((R.S.C, '_fit'), (R.S.T, '_get'),
                             (R.S.B.subprocess, 'run'), (R.S.B.subprocess, 'Popen')):
            item = patch.object(target, name, side_effect=AssertionError('runtime prohibited'))
            item.start(); self.addCleanup(item.stop)

    def put(self, token):
        directory = self.fixture.state / token
        directory.mkdir(mode=0o700)
        records = deepcopy(self.fixture.records)
        records['intent.json']['argv'] = R.S.B.arguments(directory)
        records['status.json']['evidence_token'] = token
        records['result.json']['attempt_token'] = token
        for name, value in records.items():
            R.S.N._write(directory / name, value)

    def snapshot(self):
        result = {}
        for path in self.fixture.lab.rglob('*'):
            info = path.lstat()
            result[str(path)] = (R.R._signature(info),
                hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() and not path.is_symlink() else None)
        return result

    def invoke(self, args=None):
        output = io.StringIO()
        with patch.object(R.sys, 'stdout', output):
            code = R.main([] if args is None else args)
        self.assertEqual(len(output.getvalue().splitlines()), 1)
        return code, json.loads(output.getvalue())

    def test_two_comparisons_sorted_by_token_safe_projection_and_prediction_link(self):
        self.put('1' * 32)
        before = self.snapshot()
        with patch.object(R.S.N, '_write', side_effect=AssertionError('write prohibited')):
            report = R.collect()
            result = P.predict(report['runs'][0]['comparison_token'],
                               dict(schema_version=1, rows=[dict(x1=0, x2=0, x3=0)]))
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(set(report), {'schema_version','profile','result','runs','unavailable','counts','error_code','scope'})
        self.assertEqual(report['counts'], dict(candidates=2, verified=2, unavailable=0))
        self.assertEqual([row['comparison_token'] for row in report['runs']], ['1' * 32, COMPARISON_TOKEN])
        for row in report['runs']:
            self.assertEqual(set(row), {'comparison_token','training_token','alpha','identity','lake_source','metrics'})
            self.assertEqual(row['alpha'], 10.0)
            self.assertEqual(row['training_token'], self.fixture.comparison['identity']['attempt_token'])
            self.assertEqual(row['metrics']['candidate'], self.fixture.comparison['candidate']['metrics'])
            self.assertEqual(row['metrics']['reference'], self.fixture.comparison['reference']['metrics'])
        self.assertEqual(result['identity'], report['runs'][0]['identity'])
        self.assertEqual(result['predictions'], [self.fixture.comparison['candidate']['model']['intercept']])
        for forbidden in (b'"coefficients"',b'"model"',b'"rows"',b'"holdout"',b'"predictions"'):
            self.assertNotIn(forbidden, R.canonical(report))

    def test_partial_invalid_and_symlink_candidates_stay_separate(self):
        (self.fixture.state / ('1' * 32)).mkdir(mode=0o700)
        self.put('2' * 32)
        (self.fixture.state / ('2' * 32) / 'status.json').write_bytes(b'{"secret":"DO_NOT_PUBLISH"}')
        (self.fixture.state / ('3' * 32)).symlink_to(self.fixture.directory, target_is_directory=True)
        self.put('4' * 32)
        (self.fixture.state / ('4' * 32)).chmod(0o755)
        code, report = self.invoke()
        self.assertEqual(code, 0)
        self.assertEqual(report['counts'], dict(candidates=5,verified=1,unavailable=4))
        self.assertEqual([row['status'] for row in report['unavailable']], ['partial','invalid','invalid','invalid'])
        self.assertTrue(all(set(row) == {'comparison_token','status','reason'} for row in report['unavailable']))
        self.assertNotIn('DO_NOT_PUBLISH', R.canonical(report).decode())

    def test_partial_includes_any_of_six_required_files_not_only_result(self):
        (self.fixture.directory / 'launch.json').unlink()
        with patch.object(R.S, 'load_saved') as loader:
            report = R.collect()
        loader.assert_not_called()
        self.assertEqual(report['unavailable'], [dict(comparison_token=COMPARISON_TOKEN,
                            status='partial', reason='COMPARISON_RUN_PARTIAL')])

    def test_invalid_source_is_unavailable_without_publishing_prior_projection(self):
        self.fixture.original.artifact.write_bytes(b'{}')
        report = R.collect()
        self.assertEqual(report['runs'], [])
        self.assertEqual(report['counts'], dict(candidates=1,verified=0,unavailable=1))

    def test_absent_root_and_absent_lab_are_complete_empty_without_writes(self):
        empty = self.fixture.original.root / 'empty-lab'; empty.mkdir(mode=0o700)
        for lab in (empty, self.fixture.original.root / 'missing-lab'):
            state = lab / 'comparisons'
            with patch.object(R.S, 'LAB_STATE', lab), patch.object(R.S, 'STATE', state), \
                    patch.object(R.S, 'load_saved') as loader:
                report = R.collect()
            loader.assert_not_called()
            self.assertEqual(report['result'], 'complete')
            self.assertEqual(report['counts'], dict(candidates=0,verified=0,unavailable=0))
            self.assertFalse(state.exists())

    def test_bad_root_mode_and_symlink_fail_without_loading(self):
        self.fixture.state.chmod(0o755)
        with patch.object(R.S, 'load_saved') as loader:
            self.assertEqual(self.invoke()[1]['error_code'], 'COMPARISON_RUNS_ROOT_INVALID')
        loader.assert_not_called(); self.fixture.state.chmod(0o700)
        alias = self.fixture.original.root / 'alias'; alias.symlink_to(self.fixture.lab, target_is_directory=True)
        with patch.object(R.S, 'LAB_STATE', alias), patch.object(R.S, 'STATE', alias / 'comparisons'):
            self.assertEqual(self.invoke()[1]['error_code'], 'COMPARISON_RUNS_ROOT_INVALID')

    def test_unknown_names_and_total_cap_refuse_whole_without_name_disclosure(self):
        extra = self.fixture.state / 'DO_NOT_PUBLISH'; extra.mkdir(mode=0o700)
        with patch.object(R.S, 'load_saved') as loader:
            code, report = self.invoke()
        loader.assert_not_called()
        self.assertEqual((code,report['error_code']), (1,'COMPARISON_RUNS_INVENTORY_INVALID'))
        self.assertNotIn('DO_NOT_PUBLISH', R.canonical(report).decode()); extra.rmdir()
        for i in range(128): (self.fixture.state / format(i, '032x')).mkdir(mode=0o700)
        with patch.object(R.S, 'load_saved') as loader:
            code, report = self.invoke()
        loader.assert_not_called()
        self.assertEqual(report['error_code'], 'COMPARISON_RUNS_ENTRY_LIMIT')
        self.assertEqual(report['counts'], dict(candidates=None,verified=0,unavailable=0))

    def test_namespace_change_discards_verified_rows(self):
        load = R.S.load_saved
        def changed(token):
            value = load(token)
            (self.fixture.directory / 'new-file').write_bytes(b'new')
            return value
        with patch.object(R.S, 'load_saved', side_effect=changed):
            code, report = self.invoke()
        self.assertEqual((code,report['error_code']), (1,'COMPARISON_RUNS_INVENTORY_CHANGED'))
        self.assertEqual(report['runs'], [])

    def test_public_limit_and_unknown_args_never_publish_partial_success(self):
        with patch.object(R, 'MAX_PUBLIC_BYTES', 512):
            code, report = self.invoke()
        self.assertEqual(report['error_code'], 'COMPARISON_RUNS_PUBLIC_LIMIT')
        self.assertEqual(report['runs'], [])
        with patch.object(R, 'collect') as collect:
            code, report = self.invoke(['--state','DO_NOT_PUBLISH'])
        collect.assert_not_called()
        self.assertEqual(report['error_code'], 'COMPARISON_RUNS_ARGUMENTS_INVALID')
        self.assertNotIn('DO_NOT_PUBLISH', R.canonical(report).decode())

    def test_first_cancellation_survives_inventory_and_output_failure(self):
        first = KeyboardInterrupt('original')
        inventory = R.inventory
        calls = []
        def changed():
            calls.append(1)
            if len(calls) > 1: raise OSError('later inventory')
            return inventory()
        class Broken:
            def write(self, value): raise OSError('later output')
        with patch.object(R.S, 'load_saved', side_effect=first), patch.object(R,'inventory',side_effect=changed), \
                patch.object(R.sys, 'stdout', Broken()):
            with self.assertRaises(KeyboardInterrupt) as caught: R.main([])
        self.assertIs(caught.exception, first)


if __name__ == '__main__':
    unittest.main()
