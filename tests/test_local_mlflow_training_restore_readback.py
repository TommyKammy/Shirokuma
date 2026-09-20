"""Training-profile restore readback through fake SDK and disposable SQLite only."""
from copy import deepcopy
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from tools.local_experiment.mlflow import restore_readback as B
from tests.test_local_mlflow_restore import fixture, refresh
from tests.test_local_mlflow_restore_readback import FakeSDK
from tests.test_local_mlflow_training_restore import add_training

R = B.R


class TrainingSDK(FakeSDK):
    def search_runs(self, experiment_ids, **kwargs):
        self.searched_experiments = set(experiment_ids)
        return super().search_runs(experiment_ids, **kwargs)

    def get_experiment(self, key):
        self.calls.append(('experiment', key))
        return super().get_experiment(key)

    def get_run(self, key):
        self.calls.append(('run', key))
        return super().get_run(key)


class TrainingReadbackTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.manifest, self.backup = fixture(self.root)
        self.training = add_training(self.root, self.backup)
        # The restore fixture is deliberately smaller than MLflow's public view.
        with sqlite3.connect(self.backup/'store/tracking.sqlite') as db:
            columns = {row[1] for row in db.execute('PRAGMA table_info(runs)')}
            for name, spec in [('name', "TEXT DEFAULT 'fixture'"), ('user_id', "TEXT DEFAULT 'tester'"),
                               ('end_time', 'INTEGER DEFAULT 1700000000010')]:
                if name not in columns:
                    db.execute('ALTER TABLE runs ADD COLUMN '+name+' '+spec)
        refresh(self.root, self.backup, self.manifest)
        restored = R.restore(self.manifest, root=self.root)
        self.token = restored['token']
        self.checked = R.inspect_completed(self.token, root=self.root)
        self.expected = B.expected_view(self.checked)
        self.sdk = TrainingSDK(self.expected)
        self.sdk.base = self.checked['clone_artifacts']

    def sdk_read(self):
        # A product import would fail even if MLflow happens to be installed.
        with patch.dict('sys.modules', {'mlflow': None}):
            return B.sdk_read(self.checked, self.expected, factory=self.sdk.factory)

    def training_ids(self):
        return [self.training[key]['run'] for key in ('3', '4')]

    def test_all_five_experiments_and_training_runs_have_complete_sdk_readback(self):
        original = R.inventory(self.backup)
        clone = B.seal(self.checked)
        result = self.sdk_read()
        self.assertEqual(result['result'], 'passed')
        self.assertEqual(set(self.expected['experiments']), {'0', '1', '2', '3', '4'})
        self.assertEqual(self.sdk.searched_experiments, set(self.expected['experiments']))
        self.assertEqual({call[1] for call in self.sdk.calls if call[0]=='experiment'}, set(self.expected['experiments']))
        self.assertEqual({call[1] for call in self.sdk.calls if call[0]=='run'}, set(self.expected['runs']))
        self.assertEqual(result['counts'], {'experiments':5, 'runs':4, 'metrics':22, 'artifacts':4})
        for run in self.training_ids():
            value = self.expected['runs'][run]
            self.assertEqual(len(value['metrics']), 9)
            self.assertEqual(len(value['histories']), 9)
            self.assertEqual({call[2] for call in self.sdk.calls if call[:2]==('history',run)}, set(value['histories']))
            self.assertIn(('download',run,''), self.sdk.calls)
        uri = 'sqlite:///'+str(self.checked['clone_db'])
        self.assertEqual(self.sdk.kwargs, {'tracking_uri':uri, 'registry_uri':uri, 'workspace_store_uri':uri})
        self.assertEqual(R.inventory(self.backup), original)
        self.assertEqual(B.seal(self.checked), clone)

    def test_readback_owner_preserves_cloned_history_and_training_receipts(self):
        before = B.seal(self.checked)
        source = R.inventory(self.backup)
        def runner(evidence, checked):
            with patch.dict('sys.modules', {'mlflow':None}):
                result = B.sdk_read(checked, B.expected_view(checked), factory=self.sdk.factory)
            R.write(evidence/'worker-result.json', R.canonical(result))
        result = B.readback(self.token, root=self.root, runner=runner)
        self.assertEqual(result['result'], 'passed')
        self.assertTrue(result['logical_unchanged'] and result['retained_unchanged'])
        self.assertEqual(result['counts']['experiments'], 5)
        self.assertEqual(B.seal(self.checked), before)
        self.assertEqual(R.inventory(self.backup), source)
        R.inspect_completed(self.token, root=self.root)

    def test_each_new_experiment_and_run_must_appear_in_search(self):
        experiments = self.sdk.search_experiments
        runs = self.sdk.search_runs
        for key in ('3', '4'):
            with self.subTest(experiment=key), patch.object(self.sdk, 'search_experiments',
                    side_effect=lambda **kw:[v for v in experiments(**kw) if v.experiment_id!=key]):
                with self.assertRaisesRegex(B.ReadbackError, 'EXPERIMENT_MISMATCH'):
                    self.sdk_read()
            run = self.training[key]['run']
            with self.subTest(run=run), patch.object(self.sdk, 'search_runs',
                    side_effect=lambda ids,**kw:[v for v in runs(ids,**kw) if v.info.run_id!=run]):
                with self.assertRaisesRegex(B.ReadbackError, 'RUN_MISMATCH'):
                    self.sdk_read()

    def test_training_parameters_tags_and_latest_metrics_are_all_bound(self):
        for run in self.training_ids():
            for field in ('params','tags','metrics'):
                with self.subTest(run=run,field=field):
                    original = deepcopy(self.sdk.expected['runs'][run][field])
                    self.sdk.expected['runs'][run][field].pop(next(iter(original)))
                    try:
                        with self.assertRaisesRegex(B.ReadbackError, 'DATA_MISMATCH'):
                            self.sdk_read()
                    finally:
                        self.sdk.expected['runs'][run][field] = original

    def test_training_history_rejects_missing_extra_wrong_value_or_boolean_timestamp(self):
        actual = self.sdk.get_metric_history
        for run in self.training_ids():
            metric = next(iter(self.expected['runs'][run]['histories']))
            for mutation in ('missing','extra','value','boolean'):
                def changed(key, name):
                    points = actual(key,name)
                    if (key,name)!=(run,metric): return points
                    if mutation=='missing': return []
                    if mutation=='extra': return points+points
                    if mutation=='value': points[0].value += 1.0
                    if mutation=='boolean': points[0].timestamp = True
                    return points
                with self.subTest(run=run,mutation=mutation), patch.object(self.sdk,'get_metric_history',side_effect=changed):
                    with self.assertRaisesRegex(B.ReadbackError, 'HISTORY_MISMATCH'):
                        self.sdk_read()

    def test_each_training_experiment_owner_tag_and_artifact_namespace_are_bound(self):
        for key in ('3','4'):
            value = self.sdk.expected['experiments'][key]
            original = deepcopy(value)
            for mutation in ('owner','namespace'):
                with self.subTest(experiment=key,mutation=mutation):
                    if mutation=='owner': value['tags']['shirokuma.owner']='different-owner'
                    else: value['artifact_location']=self.expected['experiments']['1']['artifact_location']
                    try:
                        with self.assertRaisesRegex(B.ReadbackError, 'EXPERIMENT_MISMATCH'):
                            self.sdk_read()
                    finally:
                        value.clear();value.update(deepcopy(original))

    def test_training_artifact_bytes_are_read_and_verified(self):
        for run in self.training_ids():
            folder = B.uri_path(self.expected['runs'][run]['info']['artifact_uri'],self.sdk.base)
            path = folder/'safe-projection.json'
            original = path.read_bytes()
            # Preserve length, so the content digest is required to detect this.
            changed = bytes([original[0]^1])+original[1:]
            with self.subTest(run=run):
                try:
                    path.write_bytes(changed)
                    with self.assertRaisesRegex(B.ReadbackError, 'ARTIFACT_MISMATCH'):
                        self.sdk_read()
                finally:
                    path.write_bytes(original)

    def test_training_artifact_cannot_resolve_to_another_run_namespace(self):
        actual = self.sdk.download_artifacts
        synthetic,lake = self.training_ids()
        sibling = str(B.uri_path(self.expected['runs'][synthetic]['info']['artifact_uri'],self.sdk.base))
        def redirected(key,path,dst_path=None):
            return sibling if key==lake else actual(key,path,dst_path)
        with patch.object(self.sdk,'download_artifacts',side_effect=redirected):
            with self.assertRaisesRegex(B.ReadbackError, 'ARTIFACT_REFUSED'):
                self.sdk_read()


if __name__ == '__main__':
    unittest.main()
