"""Offline model reads use the same saved evidence as Notebook reviews."""
from copy import deepcopy
import unittest
from unittest.mock import patch

from tools.local_experiment.notebook import training as T
from tests import test_notebook_lake_training_client as L
from tests import test_notebook_training_client as B


class SavedTrainingReaderTests(unittest.TestCase):
    def fixture(self, case=L.LakeTrainingReviewTests):
        value = case()
        self.addCleanup(value.doCleanups)
        value.setUp()
        return value

    def test_lake_read_is_offline_and_does_not_allocate_a_notebook_attempt(self):
        fixture = self.fixture()
        before = {p: p.read_bytes() for p in fixture.state.rglob('*') if p.is_file()}
        with patch.object(T.urllib.request, 'build_opener', side_effect=AssertionError('HTTP forbidden')) as http:
            value = T.load_saved_review(L.TOKEN, kind='iceberg')
        self.assertEqual(value, fixture.public())
        http.assert_not_called()
        self.assertEqual(before, {p: p.read_bytes() for p in before})
        self.assertEqual(list(fixture.output.iterdir()), [])

    def test_synthetic_default_remains_supported_without_http(self):
        fixture = self.fixture(B.TrainingReviewTests)
        with patch.object(T.urllib.request, 'build_opener', side_effect=AssertionError('HTTP forbidden')) as http:
            value = T.load_saved_review(B.TOKEN)
        self.assertEqual(value['profile'], T.PROFILE)
        self.assertEqual(len(value['holdout']), 32)
        self.assertNotIn('lake_source', value)
        http.assert_not_called()

    def test_incomplete_or_mismatched_receipts_are_refused(self):
        fixture = self.fixture()
        for field, value in [('phase', 'training'), ('result', 'failed'), ('token', 'e'*32), ('workload_sha256', 'e'*64)]:
            with self.subTest(field=field):
                receipt = deepcopy(fixture.receipt)
                receipt[field] = value
                fixture.write(fixture.receipt_path, receipt)
                with self.assertRaisesRegex(T.N.NotebookError, '^NOTEBOOK_TRAINING_SAVED_INVALID$'):
                    T.load_saved_review(L.TOKEN, kind='iceberg')
        self.assertEqual(fixture.calls, [])

    def test_modified_artifact_is_refused(self):
        fixture = self.fixture()
        value = deepcopy(fixture.workload)
        value['model']['intercept'] += 1
        fixture.write(fixture.artifact, value)
        with self.assertRaisesRegex(T.N.NotebookError, '^NOTEBOOK_TRAINING_SAVED_INVALID$'):
            T.load_saved_review(L.TOKEN, kind='iceberg')

    def test_source_change_during_projection_is_refused(self):
        fixture = self.fixture()
        original = T._project
        def changed(*args, **kwargs):
            value = original(*args, **kwargs)
            fixture.write(fixture.artifact, {'changed': True})
            return value
        with patch.object(T, '_project', side_effect=changed):
            with self.assertRaisesRegex(T.N.NotebookError, '^NOTEBOOK_TRAINING_SAVED_INVALID$'):
                T.load_saved_review(L.TOKEN, kind='iceberg')

    def test_unknown_kind_or_path_token_is_refused_without_reading(self):
        with patch.object(T, '_read_file') as read:
            for token, kind in [('../result', 'iceberg'), (L.TOKEN, 'unknown'), (True, 'iceberg')]:
                with self.subTest(token=token, kind=kind), self.assertRaises(T.N.NotebookError):
                    T.load_saved_review(token, kind=kind)
            read.assert_not_called()


if __name__ == '__main__':
    unittest.main()
