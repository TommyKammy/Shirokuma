"""Fake-only Registry Notebook checks; no SDK, database, fit or live Reader."""
from copy import deepcopy
from html.parser import HTMLParser
import json
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import Mock, patch

from tools.local_experiment.notebook import registry as N

TOKEN, RUN = 'a' * 32, 'b' * 32
STORE = '11111111-1111-4111-8111-111111111111'
REQUEST = {'schema_version': 1, 'rows': [
    {'x1': 0.0, 'x2': 0.0, 'x3': 0.0}, {'x1': 1.0, 'x2': -2.0, 'x3': 0.5}]}


def binding():
    return dict(training_token=TOKEN, run_id=RUN, experiment_id='4', store_id=STORE,
        workload_sha256='c' * 64, model_sha256='d' * 64,
        artifact_relpath='iceberg-training/' + RUN + '/artifacts/safe-projection.json')


def resolved():
    return dict(schema_version=1, profile=N.C.PROFILE, result='verified',
        observation='sqlite-read-only', model_name=N.MODEL_NAME, version='1',
        binding=binding(), evidence='tmp/retained/result.json', scope=N.R.SCOPE)


def prediction(request=REQUEST):
    return dict(schema_version=1, profile=N.P.PROFILE, result='passed',
        request_sha256=N.P.digest(request), identity=dict(attempt_token=TOKEN,
        run_id=RUN, experiment_id='4', store_id=STORE, workload_sha256='c' * 64),
        lake_source={}, model_sha256='d' * 64, predictions=[2.25, -3.5],
        error_code=None, scope=N.P.SCOPE)


def public():
    return dict(schema_version=1, profile=N.PROFILE, result='passed',
        registry=dict(name=N.MODEL_NAME, version='1', binding=binding()),
        request=deepcopy(REQUEST), prediction=prediction())


class Elements(HTMLParser):
    def __init__(self):
        super().__init__(); self.tags = []; self.attributes = []

    def handle_starttag(self, tag, attrs):
        self.tags.append(tag); self.attributes.extend(attrs)


class NotebookRegistryTests(unittest.TestCase):
    def test_explicit_version_resolves_once_then_predicts_bound_model(self):
        calls = []
        def resolve(name, version):
            calls.append(('resolve', name, version)); return resolved()
        def predict(token, request):
            calls.append(('predict', token, request)); return prediction(request)
        with patch.object(N.R, 'resolve', side_effect=resolve), patch.object(N.P, 'predict', side_effect=predict):
            result = N.predict_registered(N.MODEL_NAME, 1, REQUEST)
        self.assertEqual(calls, [('resolve', N.MODEL_NAME, '1'), ('predict', TOKEN, REQUEST)])
        self.assertEqual(result, public())

    def test_invalid_name_version_or_request_does_not_touch_resolver(self):
        invalid = [('other', '1', REQUEST)]
        invalid += [(N.MODEL_NAME, value, REQUEST) for value in ('', 'latest', '01', '0', '-1', True, 1.0, None)]
        invalid += [(N.MODEL_NAME, '1', value) for value in (
            {'schema_version': True, 'rows': REQUEST['rows']},
            {'schema_version': 1, 'rows': []},
            {'schema_version': 1, 'rows': REQUEST['rows'] * 33},
            {'schema_version': 1, 'rows': [{'x1': True, 'x2': 0, 'x3': 0}]},
            {'schema_version': 1, 'rows': [{'x1': float('nan'), 'x2': 0, 'x3': 0}]},
            {'schema_version': 1, 'rows': [{'x1': 0, 'x2': 0, 'x3': 0, 'sql': 'SELECT 1'}]})]
        with patch.object(N.R, 'resolve') as resolve, patch.object(N.P, 'predict') as predict:
            for args in invalid:
                with self.subTest(args=args), self.assertRaisesRegex(N.NotebookError, 'NOTEBOOK_REGISTRY_ARGUMENTS_INVALID'):
                    N.predict_registered(*args)
        resolve.assert_not_called(); predict.assert_not_called()

    def test_unknown_version_never_predicts_or_falls_back(self):
        with patch.object(N.R, 'resolve', side_effect=N.C.RegistryError('REGISTRY_VERSION_NOT_FOUND')) as resolve, \
                patch.object(N.P, 'predict') as predict:
            with self.assertRaisesRegex(N.NotebookError, 'NOTEBOOK_REGISTRY_UNAVAILABLE'):
                N.predict_registered(N.MODEL_NAME, '8', REQUEST)
        resolve.assert_called_once_with(N.MODEL_NAME, '8'); predict.assert_not_called()

    def test_inexact_resolution_never_predicts(self):
        for key, value in [('schema_version', True), ('model_name', 'foreign'), ('version', '2'),
                           ('profile', 'foreign'), ('observation', 'saved-registration'), ('result', 'failed')]:
            item = resolved(); item[key] = value
            with self.subTest(key=key), patch.object(N.R, 'resolve', return_value=item), \
                    patch.object(N.P, 'predict') as predict:
                with self.assertRaises(N.NotebookError): N.predict_registered(N.MODEL_NAME, '1', REQUEST)
                predict.assert_not_called()
        item = resolved(); item['binding']['artifact_relpath'] = '../safe-projection.json'
        with patch.object(N.R, 'resolve', return_value=item), patch.object(N.P, 'predict') as predict:
            with self.assertRaises(N.NotebookError): N.predict_registered(N.MODEL_NAME, '1', REQUEST)
            predict.assert_not_called()

    def test_prediction_identity_and_model_must_match_resolved_binding(self):
        changes = [('attempt_token', 'e' * 32), ('run_id', 'e' * 32), ('store_id', 'foreign'),
                   ('experiment_id', '3'), ('workload_sha256', 'e' * 64)]
        for key, value in changes + [('model_sha256', 'e' * 64)]:
            item = prediction()
            if key == 'model_sha256': item[key] = value
            else: item['identity'][key] = value
            with self.subTest(key=key), patch.object(N.R, 'resolve', return_value=resolved()), \
                    patch.object(N.P, 'predict', return_value=item):
                with self.assertRaisesRegex(N.NotebookError, 'NOTEBOOK_REGISTRY_RESULT_INVALID'):
                    N.predict_registered(N.MODEL_NAME, '1', REQUEST)

    def test_prediction_shape_request_hash_and_numbers_are_checked(self):
        for key, value in [('request_sha256', 'f' * 64), ('predictions', []),
                           ('predictions', [True, 0]), ('predictions', [float('inf'), 0]),
                           ('result', 'failed'), ('schema_version', True)]:
            item = public(); item['prediction'][key] = value
            with self.subTest(key=key, value=value), self.assertRaises(N.NotebookError):
                N.render_prediction(item)

    def test_first_cancellation_is_preserved(self):
        for phase in ('resolve', 'predict'):
            original = KeyboardInterrupt()
            with patch.object(N.R, 'resolve', side_effect=original if phase == 'resolve' else None,
                              return_value=resolved()), \
                    patch.object(N.P, 'predict', side_effect=original if phase == 'predict' else None,
                                 return_value=prediction()) as predict:
                with self.assertRaises(KeyboardInterrupt) as caught:
                    N.predict_registered(N.MODEL_NAME, '1', REQUEST)
                self.assertIs(caught.exception, original)
                if phase == 'resolve': predict.assert_not_called()

    def test_render_is_pure_compact_table_and_refuses_injected_identity(self):
        with patch.object(N.R, 'resolve') as resolve, patch.object(N.P, 'predict') as predict:
            markup = N.render_prediction(public())
        resolve.assert_not_called(); predict.assert_not_called()
        parsed = Elements(); parsed.feed(markup)
        self.assertEqual(parsed.tags.count('table'), 1)
        self.assertEqual(parsed.tags.count('tr'), 3)
        self.assertIn(('tabindex', '0'), parsed.attributes)
        self.assertFalse(set(parsed.tags) & {'script', 'iframe', 'img', 'style'})
        self.assertFalse(any(k.startswith('on') or k in ('src', 'href') for k, _ in parsed.attributes))
        self.assertIn('2.25', markup); self.assertIn('-3.5', markup)
        self.assertIn(N.MODEL_NAME, markup); self.assertIn(RUN, markup)
        item = public(); item['registry']['name'] = '<script>alert(1)</script>'
        with self.assertRaises(N.NotebookError): N.render_prediction(item)

    def cells(self):
        path = Path(__file__).resolve().parents[1] / 'examples/local_registry_prediction.ipynb'
        notebook = json.loads(path.read_text())
        cells = [cell for cell in notebook['cells'] if cell['cell_type'] == 'code']
        self.assertEqual(len(cells), 3)
        for cell in cells:
            self.assertEqual(cell['outputs'], []); self.assertIsNone(cell['execution_count'])
        return [''.join(cell['source']) for cell in cells]

    def test_template_initial_run_all_has_no_registry_prediction_or_db_work(self):
        display = Mock(); module = types.SimpleNamespace(HTML=lambda value: value, display=display)
        state = {'prediction': 'old-result'}
        with patch.dict(sys.modules, {'IPython.display': module}), \
                patch.object(N, 'predict_registered') as predict, patch.object(N, 'render_prediction') as render:
            for cell in self.cells(): exec(compile(cell, '<notebook>', 'exec'), state)
        self.assertIsNone(state['prediction']); self.assertIsNone(state['prediction_html'])
        predict.assert_not_called(); render.assert_not_called(); display.assert_not_called()

    def test_template_explicit_version_then_failure_clears_stale_result(self):
        display = Mock(); module = types.SimpleNamespace(HTML=lambda value: value, display=display)
        state = {}; cells = self.cells()
        with patch.dict(sys.modules, {'IPython.display': module}), \
                patch.object(N, 'predict_registered', return_value=public()) as predict, \
                patch.object(N, 'render_prediction', return_value='<table>result</table>') as render:
            exec(compile(cells[0], '<setup>', 'exec'), state)
            exec(compile(cells[1].replace('model_version = ""', 'model_version = "1"'), '<selection>', 'exec'), state)
            exec(compile(cells[2], '<prediction>', 'exec'), state)
            self.assertEqual(state['prediction'], public()); display.assert_called_once()
            predict.side_effect = N.NotebookError('NOTEBOOK_REGISTRY_UNAVAILABLE')
            state['model_version'] = '999'
            exec(compile(cells[2], '<failed-prediction>', 'exec'), state)
            self.assertIsNone(state['prediction']); self.assertIsNone(state['prediction_html'])
            self.assertIsNone(state['candidate']); self.assertIsNone(state['candidate_html'])
            self.assertEqual(display.call_count, 1)
            predict.side_effect = None
            render.side_effect = N.NotebookError('NOTEBOOK_REGISTRY_RESULT_INVALID')
            exec(compile(cells[2], '<failed-render>', 'exec'), state)
            self.assertIsNone(state['prediction']); self.assertIsNone(state['candidate'])
            self.assertIsNone(state['prediction_html']); self.assertIsNone(state['candidate_html'])
            self.assertEqual(display.call_count, 1)
            exec(compile(cells[1], '<cleared-selection>', 'exec'), state)
            self.assertEqual(state['model_version'], ''); self.assertIsNone(state['prediction'])
            self.assertIsNone(state['prediction_html'])


    def test_template_render_cancellation_or_unexpected_error_clears_intermediates(self):
        for original in (KeyboardInterrupt('cancel render'), RuntimeError('render failed')):
            with self.subTest(error=type(original).__name__):
                display = Mock()
                module = types.SimpleNamespace(HTML=lambda value: value, display=display)
                state = {}; cells = self.cells()
                with patch.dict(sys.modules, {'IPython.display': module}), \
                        patch.object(N, 'predict_registered', return_value=public()) as predict, \
                        patch.object(N, 'render_prediction', side_effect=original) as render:
                    exec(compile(cells[0], '<setup>', 'exec'), state)
                    exec(compile(cells[1].replace('model_version = ""', 'model_version = "1"'), '<selection>', 'exec'), state)
                    with self.assertRaises(type(original)) as caught:
                        exec(compile(cells[2], '<interrupted-render>', 'exec'), state)
                    self.assertIs(caught.exception, original)
                    predict.assert_called_once(); render.assert_called_once(); display.assert_not_called()
                    for key in ('prediction', 'prediction_html', 'candidate', 'candidate_html'):
                        self.assertIsNone(state[key], key)


if __name__ == '__main__':
    unittest.main()
