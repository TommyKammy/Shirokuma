"""Schema discovery uses the existing Reader owner; no real child or service."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from tools.local_experiment.notebook import client as N
from tests.test_local_notebook_client import Child, encoded

TOKEN = 'a' * 32


def inventory(schema='smoke', count=1, *, complete=True):
    return {'schema_version': 1, 'catalog': 'iceberg', 'schema': schema,
            'tables': [f'{schema}.table_{number:03d}' for number in range(count)],
            'complete': complete}


def frames(public=None, *, result='passed', cleanup=True):
    return [dict(schema_version=1, event='allocated', command='query tables', evidence_token=TOKEN),
            dict(schema_version=1, event='result', command='query tables', evidence_token=TOKEN,
                 result=result, observation=(inventory() if public is None else public) if result == 'passed' else None,
                 error_code=None if result == 'passed' else 'INTERRUPTED' if result == 'interrupted' else 'QUERY_FAILED',
                 cleanup_verified=cleanup, evidence=f'tmp/local-experiment/query-read/{TOKEN}/summary.json')]


class MetadataClientTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.output = Path(temporary.name).resolve()
        self.output.chmod(0o700)
        self.binary = self.output / 'shirokuma'
        self.binary.write_bytes(b'fake-not-executable')
        environment = patch.dict(os.environ, {'SHIROKUMA_NOTEBOOK_RUN_DIR': str(self.output),
                                              'SHIROKUMA_NOTEBOOK_BINARY': str(self.binary)})
        environment.start()
        self.addCleanup(environment.stop)

    def launch(self, child, saved):
        def fake(argv, **kwargs):
            self.assertTrue(kwargs['start_new_session'])
            intent = json.loads((self.output / 'reader-intent.json').read_bytes())
            self.assertEqual(intent['command'], 'query tables')
            kwargs['stdout'].write(encoded(saved))
            return child
        return fake

    def test_owned_list_exact_schema_argv_once_and_private_receipt(self):
        public = inventory('local_experiment')
        with patch.object(N.subprocess, 'Popen', side_effect=self.launch(Child(), frames(public))) as launch, \
                patch.object(N, '_process_start', return_value='synthetic child start'):
            result = N.read_tables('local_experiment')
            with self.assertRaises(FileExistsError):
                N.read_tables('local_experiment')
        self.assertEqual(launch.call_count, 1)
        self.assertEqual(result, public)
        self.assertEqual(launch.call_args.args[0], [str(self.binary), '--repo-root', str(N.ROOT),
            '--profile', 'local-experiment', 'query', 'tables', '--schema', 'local_experiment'])
        self.assertEqual(json.loads((self.output / 'reader-intent.json').read_bytes()),
                         {'schema_version': 1, 'command': 'query tables',
                          'request': {'command': 'tables', 'schema': 'local_experiment'}})
        status = json.loads((self.output / 'reader-status.json').read_bytes())
        self.assertEqual(status['state'], 'passed')
        self.assertTrue(status['child_exited'] and status['cleanup_verified'])
        for name in ('reader-intent.json', 'reader-status.json', 'reader.stdout.jsonl'):
            self.assertEqual((self.output / name).stat().st_mode & 0o777, 0o600)

    def test_both_schemas_empty_and_exact_limit_preserve_completeness(self):
        for schema in N.C.SCHEMAS:
            for count, complete in ((0, True), (1, True), (100, True), (100, False)):
                with self.subTest(schema=schema, count=count, complete=complete):
                    public = inventory(schema, count, complete=complete)
                    token, observed, cleanup = N._tables_reader_frames(encoded(frames(public)), 0, schema)
                    self.assertEqual(token, TOKEN)
                    self.assertEqual(observed, public)
                    self.assertTrue(cleanup)
                    with patch.object(N.subprocess, 'Popen') as child:
                        self.assertEqual(N._tables_public(public, schema), public)
                    child.assert_not_called()

    def test_inventory_unknown_fields_types_limits_order_and_schema_are_rejected(self):
        malformed = [dict(inventory(), extra='SELECT 1'), dict(inventory(), schema_version=True),
                     dict(inventory(), catalog='other'), inventory('local_experiment'),
                     dict(inventory(), tables='smoke.table_000'), dict(inventory(), complete=1),
                     inventory(count=101), inventory(count=0, complete=False),
                     inventory(count=99, complete=False),
                     dict(inventory(), tables=['smoke.z', 'smoke.a']),
                     dict(inventory(), tables=['smoke.a', 'smoke.a']),
                     dict(inventory(), tables=['local_experiment.a']),
                     dict(inventory(), tables=['iceberg.smoke.a']),
                     dict(inventory(), tables=['smoke.a;DROP TABLE x']),
                     dict(inventory(), tables=[False]), dict(inventory(), tables=[{}]),
                     dict(inventory(), tables=['smoke.' + 'a' * 64])]
        for public in malformed:
            with self.subTest(public=public), self.assertRaises(N.NotebookError):
                N._tables_reader_frames(encoded(frames(public)), 0, 'smoke')
            with self.subTest(saved=public), self.assertRaises(N.NotebookError):
                N._tables_public(public, 'smoke')
        for invalid in (None, [], False):
            with self.assertRaises(N.NotebookError):
                N._tables_public(invalid, 'smoke')

    def test_envelope_binding_rejects_wrong_command_token_exit_or_evidence(self):
        for key, value in (('command', 'query describe'), ('schema_version', 4),
                           ('evidence_token', 'b' * 32), ('evidence', 'tmp/not-ours'),
                           ('cleanup_verified', False)):
            saved = frames()
            saved[-1][key] = value
            with self.subTest(key=key), self.assertRaises(N.NotebookError):
                N._tables_reader_frames(encoded(saved), 0, 'smoke')
        for raw, code in ((encoded(frames()), 1), (encoded(frames())[:-1], 0),
                          (encoded(frames() + [frames()[-1]]), 0)):
            with self.assertRaises(N.NotebookError):
                N._tables_reader_frames(raw, code, 'smoke')

    def test_invalid_schema_fails_before_allocation_or_child(self):
        for schema in ('other', 'iceberg.smoke', 'smoke;SELECT 1', '', None, True, []):
            with patch.object(N.subprocess, 'Popen') as launch, self.assertRaises(N.NotebookError):
                N.read_tables(schema)
            launch.assert_not_called()
            self.assertFalse((self.output / 'reader-intent.json').exists())

    def test_failed_read_is_unavailable_not_empty_even_with_verified_cleanup(self):
        for cleanup in (False, True):
            token, observed, closed = N._tables_reader_frames(
                encoded(frames(result='failed', cleanup=cleanup)), 1, 'smoke')
            self.assertEqual(token, TOKEN)
            self.assertIsNone(observed)
            self.assertIs(closed, cleanup)
        saved = frames(result='failed')
        saved[-1]['observation'] = inventory(count=0)
        with self.assertRaises(N.NotebookError):
            N._tables_reader_frames(encoded(saved), 1, 'smoke')

    def test_first_interrupt_preserved_after_reader_drain_and_secondary_signal_error(self):
        first = KeyboardInterrupt()
        with patch.object(N.subprocess, 'Popen', side_effect=self.launch(Child(initial=first), frames(result='interrupted'))), \
                patch.object(N, '_process_start', return_value='synthetic child start'), \
                patch.object(N.os, 'killpg', side_effect=OSError('secondary')), \
                self.assertRaises(KeyboardInterrupt) as caught:
            N.read_tables('smoke')
        self.assertIs(caught.exception, first)
        status = json.loads((self.output / 'reader-status.json').read_bytes())
        self.assertEqual(status['state'], 'interrupted')
        self.assertTrue(status['child_exited'] and status['cleanup_verified'])

    def test_timeout_unknown_cleanup_never_returns_inventory(self):
        first = subprocess.TimeoutExpired('fixed Reader', 240)
        child = Child(initial=first, stays_alive=True)
        with patch.object(N.subprocess, 'Popen', side_effect=self.launch(child, frames())), \
                patch.object(N, '_process_start', return_value='synthetic child start'), \
                patch.object(N, 'CLEANUP_TIMEOUT', 0), \
                patch.object(N.os, 'killpg') as signal, self.assertRaises(N.NotebookError):
            N.read_tables('smoke')
        self.assertEqual(signal.call_count, 1)
        status = json.loads((self.output / 'reader-status.json').read_bytes())
        self.assertFalse(status['cleanup_verified'])
        self.assertFalse(status['child_exited'])


if __name__ == '__main__':
    unittest.main()
