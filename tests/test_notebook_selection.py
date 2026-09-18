"""Pure selection/private file tests; no Reader, service or notebook kernel."""
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tools.local_experiment.notebook import selection as S


def request():
    return {'command': 'select', 'table': 'local_experiment.preview_table',
            'columns': ['name', 'id'], 'limit': 3}


class NotebookSelectionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name).resolve()
        self.path = self.directory / 'request.json'

    def write(self, raw=None):
        raw = S.canonical(request()) if raw is None else raw
        self.path.write_bytes(raw)
        self.path.chmod(0o600)
        return hashlib.sha256(raw).hexdigest()

    def refused(self, function, *args, code=None):
        with self.assertRaises(S.SelectionError) as caught:
            function(*args)
        self.assertIn(str(caught.exception), S.ERROR_CODES)
        self.assertNotIn('PRIVATE_INPUT_CANARY', str(caught.exception))
        if code:
            self.assertEqual(str(caught.exception), code)

    def test_current_head_select_reuses_reader_and_preserves_column_order(self):
        value = request(); saved = deepcopy(value)
        normalized = S.normalize_request(value)
        self.assertEqual(normalized, value)
        normalized['columns'].reverse()
        self.assertEqual(value, saved)
        for limit in (1, 100):
            self.assertEqual(S.normalize_request({**value, 'limit': limit})['limit'], limit)
        self.assertEqual(S.normalize_request({**value, 'table': 'smoke.fixture_v1'})['table'], 'smoke.fixture_v1')

    def test_unsupported_profiles_and_types_are_rejected_without_input_echo(self):
        values = [None, [], {}, {'command': 'tables', 'schema': 'smoke'},
                  {'command': 'describe', 'table': 'smoke.fixture_v1'}]
        values += [{**request(), key: value} for key, value in [
            ('table', 'private.foo'), ('table', 'smoke.PRIVATE_INPUT_CANARY;DROP'),
            ('table', '--profile'), ('columns', ['a,b']), ('columns', ['id', 'id']),
            ('columns', []), ('columns', [True]), ('columns', 'id'), ('columns', ['x'] * 17),
            ('limit', True), ('limit', 0), ('limit', 101), ('limit', 1.0), ('limit', '1'),
            ('where', None), ('snapshot_id', None), ('sql', 'PRIVATE_INPUT_CANARY'),
            ('command', 'count')]]
        for value in values:
            self.refused(S.normalize_request, value)

    def test_parse_is_one_bounded_strict_json_document(self):
        self.assertEqual(S.parse_request(json.dumps(request(), indent=2).encode()), request())
        for raw in [b'', b'\xff', b'{}{}', b'NaN', b'null', b' ' * (S.MAX_REQUEST_BYTES + 1),
                    b'{"command":"select","command":"select"}', b'{"limit":Infinity}',
                    'PRIVATE_INPUT_CANARY']:
            self.refused(S.parse_request, raw)

    def test_canonical_ascii_newline_and_digest_bind_exact_bytes(self):
        expected = b'{"columns":["name","id"],"command":"select","limit":3,"table":"local_experiment.preview_table"}\n'
        self.assertEqual(S.canonical(request()), expected)
        self.assertEqual(S.canonical_bytes(request()), expected)
        self.assertEqual(S.digest(request()), hashlib.sha256(expected).hexdigest())
        self.assertNotEqual(S.digest(request()), hashlib.sha256(expected[:-1]).hexdigest())
        self.assertNotEqual(S.digest(request()), S.digest({**request(), 'columns': ['id', 'name']}))

    def test_arguments_are_a_fixed_list_with_no_sql_snapshot_or_filter(self):
        args = S.arguments(self.directory/'shirokuma', self.directory, request())
        self.assertEqual(args, [str(self.directory/'shirokuma'), '--repo-root', str(self.directory),
                               '--profile', 'local-experiment', 'query', 'select',
                               '--table', 'local_experiment.preview_table', '--columns', 'name,id', '--limit', '3'])
        for value in [{**request(), 'columns': ['id,--equals=PRIVATE_INPUT_CANARY']},
                      {**request(), 'table': '--snapshot-id'}, {**request(), 'equals': 'PRIVATE_INPUT_CANARY'}]:
            self.refused(S.arguments, self.directory/'shirokuma', self.directory, value)
        for path in ('relative', '/tmp/../PRIVATE_INPUT_CANARY', '/tmp/PRIVATE_INPUT_CANARY\nflag', None):
            self.refused(S.arguments, path, self.directory, request())

    def test_load_private_exact_file_preserves_bytes_and_metadata(self):
        sha = self.write()
        before = self.path.stat(); original = self.path.read_bytes()
        self.assertEqual(S.load_request(self.path, sha), request())
        self.assertEqual(self.path.read_bytes(), original)
        after = self.path.stat()
        self.assertEqual((before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns, before.st_mode),
                         (after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns, after.st_mode))

    def test_load_rejects_wrong_hash_and_noncanonical_but_valid_request(self):
        sha = self.write()
        for wrong in ('x', sha.upper(), None, True):
            self.refused(S.load_request, self.path, wrong, code='NOTEBOOK_SELECTION_HASH_INVALID')
        self.refused(S.load_request, self.path, '0'*64, code='NOTEBOOK_SELECTION_CHANGED')
        for raw in [json.dumps(request(), indent=2).encode(), S.canonical(request())[:-1], S.canonical(request())+b'\n']:
            sha = self.write(raw)
            self.refused(S.load_request, self.path, sha, code='NOTEBOOK_SELECTION_NONCANONICAL')

    def test_load_refuses_symlink_file_parent_hardlink_mode_and_nonregular(self):
        sha = self.write()
        link = self.directory/'link.json'; link.symlink_to(self.path)
        self.refused(S.load_request, link, sha)
        parent_link = self.directory/'linked'; parent_link.symlink_to(self.directory, target_is_directory=True)
        self.refused(S.load_request, parent_link/'request.json', sha)
        hard = self.directory/'hard.json'; os.link(self.path, hard)
        self.refused(S.load_request, self.path, sha)
        hard.unlink()
        self.path.chmod(0o644)
        self.refused(S.load_request, self.path, sha)
        self.path.chmod(0o600)
        self.refused(S.load_request, self.directory, sha)
        fifo = self.directory/'fifo'; os.mkfifo(fifo, 0o600)
        self.refused(S.load_request, fifo, sha)
        with patch.object(S.os, 'getuid', return_value=os.getuid()+1):
            self.refused(S.load_request, self.path, sha)

    def test_load_refuses_empty_oversized_and_invalid_json_files(self):
        for raw in (b'', b'x'*(S.MAX_REQUEST_BYTES+1), b'PRIVATE_INPUT_CANARY'):
            sha = self.write(raw)
            self.refused(S.load_request, self.path, sha)

    def test_same_length_file_mutation_during_read_is_refused(self):
        sha = self.write(); real_read = os.read; changed = False
        def read(fd, size):
            nonlocal changed
            result = real_read(fd, size)
            if not changed:
                changed = True
                self.path.write_bytes(result.replace(b'"limit":3', b'"limit":4'))
                self.path.chmod(0o600)
            return result
        with patch.object(S.os, 'read', side_effect=read):
            self.refused(S.load_request, self.path, sha, code='NOTEBOOK_SELECTION_CHANGED')

    def test_replacement_after_owned_fd_close_is_refused(self):
        sha = self.write(); real_close = os.close
        def close(fd):
            real_close(fd)
            replacement = self.directory/'replacement.json'
            replacement.write_bytes(S.canonical(request())); replacement.chmod(0o600)
            os.replace(replacement, self.path)
        with patch.object(S.os, 'close', side_effect=close):
            self.refused(S.load_request, self.path, sha, code='NOTEBOOK_SELECTION_CHANGED')

    def test_first_cancellation_survives_secondary_close_failure(self):
        sha = self.write(); original = KeyboardInterrupt(); real_close = os.close
        def close(fd):
            real_close(fd)
            raise OSError('PRIVATE_INPUT_CANARY')
        with patch.object(S.os, 'read', side_effect=original), patch.object(S.os, 'close', side_effect=close):
            with self.assertRaises(KeyboardInterrupt) as caught:
                S.load_request(self.path, sha)
        self.assertIs(caught.exception, original)

    def test_preview_template_has_fixed_empty_output_code_and_no_embedded_selection(self):
        root = Path(__file__).resolve().parents[1]
        raw = (root/'examples/local_table_preview.ipynb').read_bytes()
        value = S.validate_template(raw)
        self.assertEqual(hashlib.sha256(raw).hexdigest(), S.TEMPLATE_SHA256)
        self.assertEqual((value['nbformat'],value['nbformat_minor']), (4,5))
        self.assertEqual(len(value['cells']), 6)
        code = [cell for cell in value['cells'] if cell['cell_type']=='code']
        self.assertEqual(len(code), 3)
        self.assertTrue(all(cell['outputs']==[] and cell['execution_count'] is None for cell in code))
        self.assertEqual([''.join(cell['source']) for cell in code], [
            'from IPython.display import JSON, display\nfrom tools.local_experiment.notebook.client import read_requested_table, read_evaluations',
            'preview = read_requested_table()\ndisplay(JSON(preview))',
            'evaluations = read_evaluations()\ndisplay(JSON(evaluations))'])
        self.assertNotIn('smoke.fixture_v1', json.dumps(value))
        self.assertNotIn('local_experiment.preview_table', json.dumps(value))

    def test_preview_code_metadata_outputs_and_reencoding_cannot_replace_registered_template(self):
        root = Path(__file__).resolve().parents[1]
        raw = (root/S.TEMPLATE_PATH).read_bytes()
        for value in (raw+b' ', b'PRIVATE_INPUT_CANARY', raw.decode(), raw.replace(b'read_requested_table()', b'read_fixture()')):
            self.refused(S.validate_template, value, code='NOTEBOOK_SELECTION_TEMPLATE_INVALID')
        for field, replacement in [('outputs', [{'output_type': 'stream', 'text': 'PRIVATE_INPUT_CANARY'}]),
                                   ('execution_count', 1), ('metadata', {'arbitrary': True})]:
            value = json.loads(raw)
            value['cells'][1][field] = replacement
            self.refused(S.validate_template, json.dumps(value).encode(), code='NOTEBOOK_SELECTION_TEMPLATE_INVALID')


if __name__ == '__main__':
    unittest.main()
