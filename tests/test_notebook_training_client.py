"""Offline saved-training review tests; no SDK, training, service or kernel.

The 128-row orthogonal arithmetic fixture is self-contained. Its registered
dataset/split fingerprints replace production profile fingerprints only here.
"""
from copy import deepcopy
import hashlib
import json
import math
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch
import urllib.parse
import xml.etree.ElementTree as ET

from tools.local_experiment.notebook import client as N
from tools.local_experiment.notebook import training as T

TOKEN = 'a' * 32
RUN = 'b' * 32
STORE = '00000000-0000-0000-0000-000000000001'


def fixture_workload():
    rows = []
    for i in range(128):
        x = [float(1 if (i >> j) & 1 else -1) for j in range(3)]
        rows.append([*x, 4.0 + 2.5*x[0] - 1.75*x[1] + 0.5*x[2]])
    coefficients = [value * 96/97 for value in (2.5, -1.75, 0.5)]
    predictions = [4.0 + sum(row[j]*coefficients[j] for j in range(3)) for row in rows]

    def metrics(indices, prediction):
        target = [rows[i][3] for i in indices]
        mean = sum(target)/len(target)
        errors = [actual-estimate for actual, estimate in zip(target, prediction)]
        squared = sum(error*error for error in errors)
        return dict(mae=sum(abs(error) for error in errors)/len(errors),
                    rmse=math.sqrt(squared/len(errors)),
                    r2=1-squared/sum((actual-mean)**2 for actual in target))

    dataset = dict(feature_names=['x1', 'x2', 'x3'], target_name='target', rows=rows)
    split = dict(seed=1730, train_indices=list(range(96)), test_indices=list(range(96, 128)))
    digest = lambda value: hashlib.sha256(N._canonical(value)[:-1]).hexdigest()
    return dict(schema_version=1, profile='fixed-supervised-regression-v1', dataset=dataset, split=split,
                params=dict(algorithm='Ridge', alpha=1.0, solver='svd', fit_intercept=True, dataset_seed=1729),
                model=dict(coefficients=coefficients, intercept=4.0), baseline=dict(prediction=4.0),
                metrics=dict(train=metrics(range(96), predictions[:96]),
                             test=metrics(range(96, 128), predictions[96:]),
                             baseline_test=metrics(range(96, 128), [4.0]*32)),
                test_predictions=predictions[96:],
                hashes=dict(dataset_sha256=digest(dataset), split_sha256=digest(split)),
                versions=dict(python='3.12.12', numpy='2.5.3', scikit_learn='1.9.1'))


class Response:
    status = 200

    def __init__(self, value):
        self.raw = N._canonical(value)
        self.closed = False
        self.on_read = self.on_close = None

    def read(self, maximum):
        if self.on_read:
            self.on_read()
        return self.raw[:maximum]

    def close(self):
        self.closed = True
        if self.on_close:
            self.on_close()


class TrainingReviewTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.state = self.root/'tracking'
        self.output = self.root/'notebook'
        self.mkdir(self.state); self.mkdir(self.output)
        self.workload = fixture_workload()
        for name, value in [('STATE', self.state), ('DATASET_SHA256', self.workload['hashes']['dataset_sha256']),
                            ('SPLIT_SHA256', self.workload['hashes']['split_sha256'])]:
            changed = patch.object(T, name, value)
            changed.start(); self.addCleanup(changed.stop)
        changed = patch.dict(os.environ, {T.TOKEN_ENV:TOKEN, 'SHIROKUMA_NOTEBOOK_RUN_DIR':str(self.output)})
        changed.start(); self.addCleanup(changed.stop)
        self.receipt = dict(schema_version=1, result='verified', phase='verified', token=TOKEN,
                            store_id=STORE, source_sha256={name:'0'*64 for name in T.SOURCE_NAMES},
                            experiment_id='3', run_id=RUN, started_ms=1000, training_finished_ms=1100,
                            finished_ms=1200, workload_sha256=hashlib.sha256(N._canonical(self.workload)).hexdigest(),
                            failure_code=None)
        self.receipt_path = self.state/'training'/TOKEN/'result.json'
        self.workload_path = self.receipt_path.with_name('workload.json')
        self.artifact = self.state/'store/artifacts/supervised-training'/RUN/'artifacts/safe-projection.json'
        self.write(self.receipt_path, self.receipt)
        self.write(self.workload_path, self.workload)
        self.write(self.artifact, self.workload)
        self.marker = dict(schema_version=1, owner='shirokuma-local-mlflow', mlflow_version='3.16.1',
                           store_id=STORE, backend_uri='sqlite:///'+str(self.state/'store/tracking.sqlite'),
                           artifact_root=str(self.state/'store/artifacts'), url=N.URL)
        self.write(self.state/'store.json', self.marker)
        pairs = lambda values:[dict(key=key, value=value) for key, value in values.items()]
        self.experiment = dict(experiment=dict(experiment_id='3', name=T.EXPERIMENT, lifecycle_stage='active',
            artifact_location=(self.state/'store/artifacts/supervised-training').as_uri(),
            tags=pairs({'shirokuma.owner':T.OWNER, 'shirokuma.store_id':STORE})))
        params = dict(algorithm='Ridge', alpha='1.0', solver='svd', fit_intercept='true', dataset_seed='1729',
            dataset_sha256=self.workload['hashes']['dataset_sha256'], split_sha256=self.workload['hashes']['split_sha256'],
            train_rows='96', test_rows='32', feature_names='x1,x2,x3', split_seed='1730',
            workload='fixed-supervised-regression-v1', model_artifact='safe-projection.json',
            scope='fixed-synthetic-holdout; not general model quality',
            version_python='3.12.12', version_numpy='2.5.3', version_scikit_learn='1.9.1')
        tags = {'shirokuma.owner':T.OWNER, 'shirokuma.store_id':STORE, 'shirokuma.attempt':TOKEN,
                'shirokuma.workload_sha256':self.receipt['workload_sha256'],
                'shirokuma.time_semantics':'actual-local-training-and-tracking', 'mlflow.runName':'fixed-ridge-'+TOKEN[:8]}
        metrics = {group+'_'+key:value for group, values in self.workload['metrics'].items() for key, value in values.items()}
        self.run = dict(run=dict(info=dict(run_id=RUN, experiment_id='3', status='FINISHED', lifecycle_stage='active',
                    artifact_uri=self.artifact.parent.as_uri(), start_time=1000, end_time=1200),
                    data=dict(params=pairs(params), tags=pairs(tags), metrics=pairs(metrics))))
        self.responses = [Response(self.experiment), Response(self.run)]
        self.calls = []
        changed = patch.object(T.urllib.request, 'build_opener', side_effect=self.opener)
        changed.start(); self.addCleanup(changed.stop)

    def mkdir(self, path):
        path.mkdir(parents=True, exist_ok=True)
        for directory in [path, *path.parents]:
            if directory == self.root or not directory.is_relative_to(self.root):
                break
            directory.chmod(0o700)

    def write(self, path, value):
        self.mkdir(path.parent)
        path.write_bytes(value if isinstance(value, bytes) else N._canonical(value))
        path.chmod(0o600)

    def opener(self, *handlers):
        self.assertEqual(len(handlers), 2)
        self.assertIsInstance(handlers[0], T.urllib.request.ProxyHandler)
        self.assertEqual(handlers[0].proxies, {})
        self.assertIsInstance(handlers[1], N._NoRedirect)
        return self

    def open(self, request, timeout):
        self.assertEqual(request.get_method(), 'GET')
        self.assertIsNone(request.data)
        self.assertEqual(timeout, 10)
        self.calls.append(request.full_url)
        self.assertLessEqual(len(self.calls), 2, 'No retry or search is permitted')
        return self.responses[len(self.calls)-1]

    def public(self):
        return T._project(self.receipt, self.workload)

    def reset_attempt(self):
        for name in ('training-intent.json', 'training-review.json'):
            (self.output/name).unlink(missing_ok=True)
        self.calls.clear()

    def assert_refused(self, calls=0):
        with self.assertRaisesRegex(N.NotebookError, '^NOTEBOOK_TRAINING_READ_FAILED$'):
            T.read_training()
        self.assertEqual(len(self.calls), calls)
        self.assertFalse((self.output/'training-review.json').exists())

    def test_read_explicit_owned_run_and_projection(self):
        before = {path:path.read_bytes() for path in (self.receipt_path, self.workload_path, self.artifact, self.state/'store.json')}
        value = T.read_training()
        self.assertEqual(value, self.public())
        self.assertEqual([row['row_index'] for row in value['holdout']], list(range(96, 128)))
        self.assertEqual(set(value['metrics']), {'train', 'test', 'baseline_test'})
        self.assertEqual(value['identity']['attempt_token'], TOKEN)
        self.assertNotIn('dataset', value)
        self.assertNotIn('source_sha256', value)
        self.assertEqual(self.calls, [N.URL+'/api/2.0/mlflow/experiments/get-by-name?'+urllib.parse.urlencode({'experiment_name':T.EXPERIMENT}),
                                     N.URL+'/api/2.0/mlflow/runs/get?run_id='+RUN])
        self.assertTrue(all(response.closed for response in self.responses))
        self.assertEqual(before, {path:path.read_bytes() for path in before})
        for name in ('training-intent.json', 'training-review.json'):
            self.assertEqual(stat.S_IMODE((self.output/name).stat().st_mode), 0o600)
        self.assertEqual(N._parse((self.output/'training-review.json').read_bytes()), value)

    def test_invalid_token_rejected_before_intent_or_http(self):
        for token in ('', '../'+TOKEN, TOKEN.upper(), 'x'*32, 'a'*31, 'a'*33):
            with self.subTest(token=token), patch.dict(os.environ, {T.TOKEN_ENV:token}):
                with self.assertRaises(N.NotebookError): T.read_training()
        self.assertEqual(list(self.output.iterdir()), [])
        self.assertEqual(self.calls, [])

    def test_nonverified_or_unbound_receipt_refused_before_http(self):
        variants = [('result', 'failed'), ('phase', 'training'), ('token', 'c'*32), ('schema_version', True),
                    ('failure_code', 'failed'), ('workload_sha256', '0'*64), ('store_id', 'bad'),
                    ('run_id', 'arbitrary/path'), ('started_ms', True), ('finished_ms', 999)]
        for key, value in variants:
            with self.subTest(key=key):
                self.write(self.receipt_path, dict(self.receipt, **{key:value}))
                self.assert_refused(); self.reset_attempt()

    def test_private_marker_ownership_and_store_are_required(self):
        for key, value in [('store_id', '00000000-0000-0000-0000-000000000002'), ('url', 'http://remote'),
                           ('owner', 'other'), ('schema_version', True)]:
            with self.subTest(key=key):
                self.write(self.state/'store.json', dict(self.marker, **{key:value}))
                self.assert_refused(); self.reset_attempt()

    def test_historical_source_hashes_are_not_current_source_gates(self):
        self.receipt['source_sha256'] = {name:'f'*64 for name in T.SOURCE_NAMES}
        self.write(self.receipt_path, self.receipt)
        self.assertEqual(T.read_training()['identity']['run_id'], RUN)

    def test_workload_digest_includes_canonical_newline(self):
        raw = self.workload_path.read_bytes()
        self.assertTrue(raw.endswith(b'\n'))
        self.assertEqual(self.receipt['workload_sha256'], hashlib.sha256(raw).hexdigest())
        self.assertNotEqual(self.receipt['workload_sha256'], T._digest(self.workload))
        T.read_training()

    def test_noncanonical_json_even_with_matching_receipt_and_artifact_refused(self):
        raw = json.dumps(self.workload, indent=2).encode()
        self.write(self.workload_path, raw); self.write(self.artifact, raw)
        self.write(self.receipt_path, dict(self.receipt, workload_sha256=hashlib.sha256(raw).hexdigest()))
        self.assert_refused()

    def test_artifact_bytes_must_match(self):
        self.write(self.artifact, self.artifact.read_bytes()+b' ')
        self.assert_refused()

    def test_symlink_hardlink_and_public_file_refused(self):
        original = self.artifact.read_bytes()
        self.artifact.unlink(); self.artifact.symlink_to(self.workload_path)
        self.assert_refused(); self.reset_attempt(); self.artifact.unlink()
        os.link(self.workload_path, self.artifact)
        self.assert_refused(); self.reset_attempt(); self.artifact.unlink()
        self.write(self.artifact, original); self.artifact.chmod(0o644)
        self.assert_refused()

    def test_private_directory_boundary_is_checked(self):
        self.artifact.parent.chmod(0o755)
        self.assert_refused()

    def test_registered_dataset_and_partition_tampering_refused(self):
        cases = []
        bad = deepcopy(self.workload); bad['dataset']['rows'][127][3] += 1; cases.append(bad)
        bad = deepcopy(self.workload); bad['split']['test_indices'][0] = 0; cases.append(bad)
        bad = deepcopy(self.workload); bad['split']['train_indices'][0] = True; cases.append(bad)
        bad = deepcopy(self.workload); bad['split']['test_indices'].reverse(); cases.append(bad)
        for bad in cases:
            with self.subTest(workload=cases.index(bad)), self.assertRaises(N.NotebookError): T._workload(bad)

    def test_coefficient_fit_baseline_prediction_and_metrics_are_replayed(self):
        T._workload(self.workload)
        mutations = [lambda value:value['model']['coefficients'].__setitem__(0, 2.5),
                     lambda value:value['model'].__setitem__('intercept', 4.1),
                     lambda value:value['baseline'].__setitem__('prediction', 3.0),
                     lambda value:value['test_predictions'].__setitem__(0, 0.0),
                     lambda value:value['metrics']['train'].__setitem__('rmse', 0.0),
                     lambda value:value['metrics']['baseline_test'].__setitem__('mae', 0.0)]
        for index, mutate in enumerate(mutations):
            with self.subTest(mutation=index):
                bad = deepcopy(self.workload); mutate(bad)
                with self.assertRaises(N.NotebookError): T._workload(bad)

    def test_unknown_keys_bool_nonfinite_and_large_numbers_refused(self):
        for number in (True, float('nan'), float('inf'), -float('inf'), 10**400, 1_000_001):
            with self.subTest(number=str(number)):
                bad = deepcopy(self.workload); bad['model']['coefficients'][0] = number
                with self.assertRaises(N.NotebookError): T._workload(bad)
        for path in ('params', 'model', 'baseline', 'versions', 'dataset', 'split'):
            with self.subTest(path=path):
                bad = deepcopy(self.workload); bad[path]['unknown'] = 1
                with self.assertRaises(N.NotebookError): T._workload(bad)
        bad = dict(self.workload, unknown=1)
        with self.assertRaises(N.NotebookError): T._workload(bad)

    def test_wrong_experiment_stops_before_run_get(self):
        for key, value in [('name', N.EXPERIMENT), ('experiment_id', '9'), ('lifecycle_stage', 'deleted'),
                           ('artifact_location', 'file:///other')]:
            with self.subTest(key=key):
                bad = deepcopy(self.experiment); bad['experiment'][key] = value
                self.responses[0] = Response(bad)
                self.assert_refused(1); self.reset_attempt()

    def test_wrong_run_identity_or_lifecycle_refused(self):
        for key, value in [('run_id', 'c'*32), ('experiment_id', '2'), ('status', 'RUNNING'),
                           ('lifecycle_stage', 'deleted'), ('artifact_uri', 'file:///other'),
                           ('start_time', True), ('end_time', 1201)]:
            with self.subTest(key=key):
                bad = deepcopy(self.run); bad['run']['info'][key] = value
                self.responses[1] = Response(bad)
                self.assert_refused(2); self.reset_attempt()

    def test_run_parameters_tags_metrics_exact_and_no_duplicates(self):
        for section, replacement in [('params', 'unexpected'), ('tags', 'unexpected'), ('metrics', 99.0), ('metrics', True)]:
            with self.subTest(section=section, replacement=replacement):
                bad = deepcopy(self.run); bad['run']['data'][section][0]['value'] = replacement
                self.responses[1] = Response(bad)
                self.assert_refused(2); self.reset_attempt()
        bad = deepcopy(self.run); bad['run']['data']['metrics'].append(bad['run']['data']['metrics'][0])
        self.responses[1] = Response(bad)
        self.assert_refused(2)

    def test_upstream_json_duplicate_and_oversize_refused_without_body_leak(self):
        for raw in (b'{"experiment":{},"experiment":{"secret":"do-not-leak"}}', b' '*(N.MAX_HTTP+1)):
            with self.subTest(size=len(raw)):
                self.responses[0].raw = raw
                self.assert_refused(1); self.reset_attempt()

    def test_source_changed_during_get_prevents_publication(self):
        self.responses[1].on_read = lambda:self.write(self.receipt_path, dict(self.receipt, finished_ms=1201))
        self.assert_refused(2)

    def test_same_bytes_replacement_during_get_prevents_publication(self):
        def replace():
            pending = self.artifact.with_name('replacement.json')
            self.write(pending, self.workload)
            os.replace(pending, self.artifact)
        self.responses[1].on_read = replace
        self.assert_refused(2)

    def test_first_cancellation_survives_close_and_source_failure(self):
        first = KeyboardInterrupt('first')
        def cancelled():
            self.artifact.chmod(0o644)
            raise first
        def close_failed():
            raise RuntimeError('close failed')
        self.responses[0].on_read = cancelled
        self.responses[0].on_close = close_failed
        with self.assertRaises(KeyboardInterrupt) as caught: T.read_training()
        self.assertIs(caught.exception, first)
        self.assertTrue(self.responses[0].closed)
        self.assertEqual(len(self.calls), 1)
        self.assertFalse((self.output/'training-review.json').exists())

    def test_repeat_intent_is_not_reused_or_retried(self):
        T.read_training()
        before = (self.output/'training-review.json').read_bytes()
        with self.assertRaises(FileExistsError): T.read_training()
        self.assertEqual(len(self.calls), 2)
        self.assertEqual((self.output/'training-review.json').read_bytes(), before)

    def test_parent_rebinds_projection_without_http(self):
        value = self.public()
        T.verify_saved_review(value, TOKEN)
        self.assertEqual(self.calls, [])
        self.assertEqual(list(self.output.iterdir()), [])

    def test_parent_rejects_same_token_well_formed_projection_tampering(self):
        for mutate in (lambda value:value['identity'].__setitem__('run_id', 'c'*32),
                       lambda value:value['metrics']['train'].__setitem__('mae', 0.125),
                       lambda value:value['model']['coefficients'].__setitem__(0, 2.0),
                       lambda value:value['identity'].__setitem__('workload_sha256', '0'*64)):
            value = self.public(); mutate(value)
            T.validate_public(value)  # Shape alone deliberately cannot bind the source.
            with self.assertRaisesRegex(N.NotebookError, '^NOTEBOOK_TRAINING_REVIEW_CHANGED$'):
                T.verify_saved_review(value, TOKEN)
        self.assertEqual(self.calls, [])

    def test_parent_rejects_saved_artifact_change(self):
        value = self.public()
        self.write(self.artifact, self.artifact.read_bytes()+b' ')
        with self.assertRaisesRegex(N.NotebookError, '^NOTEBOOK_TRAINING_REVIEW_CHANGED$'):
            T.verify_saved_review(value, TOKEN)
        self.assertEqual(self.calls, [])

    def test_public_holdout_and_metrics_tampering_rejected(self):
        mutations = [lambda value:value['holdout'][0].__setitem__('residual', 99.0),
                     lambda value:value['holdout'][0].__setitem__('row_index', True),
                     lambda value:value['holdout'][0].__setitem__('actual', float('inf')),
                     lambda value:value['metrics']['test'].__setitem__('r2', 0.0),
                     lambda value:value['model'].__setitem__('train_rows', True),
                     lambda value:value['identity'].__setitem__('run_id', '<script>bad</script>')]
        for index, mutate in enumerate(mutations):
            value = self.public(); mutate(value)
            with self.subTest(mutation=index), self.assertRaises(N.NotebookError): T.validate_public(value)

    def test_tables_and_residual_svg_have_bounded_holdout_geometry(self):
        value = self.public()
        html = T.render_review(value)
        self.assertEqual(html.count('<table>'), 2)
        self.assertEqual(html.count('<tr>'), 37)
        self.assertIn('Training-mean baseline', html)
        self.assertIn(T.SCOPE, html)
        svg = ET.fromstring(T.residual_svg(value))
        circles = svg.findall('{http://www.w3.org/2000/svg}circle')
        self.assertEqual(len(circles), 32)
        for circle in circles:
            self.assertTrue(48 <= float(circle.get('cx')) <= 552)
            self.assertTrue(40 <= float(circle.get('cy')) <= 220)
            self.assertTrue(math.isfinite(float(circle.get('cy'))))
        self.assertIn('32 rows in fixed holdout order', ET.tostring(svg, encoding='unicode'))

    def test_registered_template_is_empty_and_exact(self):
        raw = T.TEMPLATE_PATH.read_bytes()
        notebook = T.validate_template(raw)
        self.assertEqual(len(notebook['cells']), 6)
        code = [cell for cell in notebook['cells'] if cell['cell_type'] == 'code']
        self.assertTrue(all(cell['outputs'] == [] and cell['execution_count'] is None for cell in code))
        with self.assertRaises(N.NotebookError): T.validate_template(raw+b' ')


if __name__ == '__main__':
    unittest.main()
