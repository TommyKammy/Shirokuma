import contextlib
from concurrent.futures import ThreadPoolExecutor
import fcntl
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools' / 'local_experiment'))
import bears_resident as b


class WapResidentTests(unittest.TestCase):
    def test_state_path_requires_writable_durable_directory(self):
        with tempfile.TemporaryDirectory() as temporary:
            durable = Path(temporary).resolve() / 'state'
            with patch.dict(os.environ, SHIROKUMA_STATE_DIR=str(durable)):
                self.assertEqual(b.wap.state_path('test.json'), durable / 'test.json')
                self.assertTrue(durable.is_dir())
                for operation in ('mkdir', 'access'):
                    manager = (patch.object(Path, 'mkdir', side_effect=OSError('read-only'))
                               if operation == 'mkdir' else patch.object(os, 'access', return_value=False))
                    with manager, self.assertRaisesRegex(RuntimeError,
                            'State directory is not writable: ' + str(durable)):
                        b.wap.state_path('test.json')

    def test_save_serializes_entire_read_modify_write(self):
        for second_name in ('orders', 'another'):
            with self.subTest(second_name=second_name), tempfile.TemporaryDirectory() as temporary:
                path = Path(temporary).resolve() / 'nested/wap_models.json'
                first_read, second_attempt, release = (threading.Event() for _ in range(3))
                real_flock = fcntl.flock
                loads = []
                def load():
                    loads.append(True)
                    if len(loads) == 1:
                        first_read.set()
                        if not release.wait(5):
                            raise RuntimeError('test writer was not released')
                    return b.wap.load_wap_registry(module)
                def flock(fd, operation):
                    if first_read.is_set():
                        second_attempt.set()
                    return real_flock(fd, operation)
                module = SimpleNamespace(WAP_REGISTRY=path, load_wap_registry=load)
                with patch.object(b.wap.fcntl, 'flock', side_effect=flock), ThreadPoolExecutor(2) as pool:
                    first = pool.submit(b.wap.save_wap_entry, module, 'orders', sql='SELECT 1')
                    try:
                        self.assertTrue(first_read.wait(5))
                        second = pool.submit(b.wap.save_wap_entry, module, second_name, published=True)
                        self.assertTrue(second_attempt.wait(5))
                        self.assertEqual(len(loads), 1)
                        self.assertFalse(path.exists())
                    finally:
                        release.set()
                    first.result(timeout=5)
                    second.result(timeout=5)
                registry = json.loads(path.read_text())
                self.assertEqual(registry['orders']['sql'], 'SELECT 1')
                self.assertTrue(registry[second_name]['published'])
                self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_history_resolves_rollback_before_newest_commit(self):
        with patch.object(b, 'sql', return_value={'rows': [[11]]}) as sql:
            self.assertEqual(b.latest_snapshot('url', 'iceberg.marts.t'), 11)
        sql.assert_called_once_with('url', 'SELECT snapshot_id FROM "iceberg"."marts"."t$history" '
                                    'ORDER BY made_current_at DESC LIMIT 1')

    def test_refs_fallback_for_missing_or_empty_history(self):
        for history in (RuntimeError('unsupported'), {'rows': []}):
            with self.subTest(history=history), patch.object(b, 'sql', side_effect=[history, {'rows': [[22]]}]) as sql:
                self.assertEqual(b.latest_snapshot('url', 'iceberg.marts.t'), 22)
                self.assertIn('"t$refs" WHERE name = \'main\'', sql.call_args.args[1])

    def test_current_snapshot_unavailable_fails_closed(self):
        for history in (RuntimeError('history unavailable'), {'rows': []}):
            for refs in (RuntimeError('refs unavailable'), {'rows': []}):
                with self.subTest(history=history, refs=refs), patch.object(
                        b, 'sql', side_effect=[history, refs]) as sql:
                    with self.assertRaisesRegex(RuntimeError, 'current active snapshot unavailable'):
                        b.latest_snapshot('url', 'iceberg.marts.t')
                self.assertEqual(sql.call_count, 2)
                self.assertFalse(any('$snapshots' in c.args[1] for c in sql.call_args_list))

    def test_pinned_audit_reads_only_selected_snapshot(self):
        with patch.object(b, 'sql', side_effect=[{'rows': [['id', 'bigint']]},
                {'rows': [[2, 0]]}, {'rows': [[1]]}]) as sql, patch.object(b, 'latest_snapshot') as latest:
            audit = b.audit_table('url', 'iceberg.sandbox.t', snapshot_id=11)
        self.assertEqual(audit['snapshot_id'], 11)
        latest.assert_not_called()
        for call in sql.call_args_list[1:]:
            self.assertIn('FOR VERSION AS OF 11', call.args[1])

    def test_existing_target_fails_closed_on_snapshot_and_baseline_errors(self):
        for failure in ('snapshot', 'empty_snapshot', 'count', 'columns'):
            with self.subTest(failure=failure):
                def latest(url, table):
                    if '.sandbox.' in table:
                        return 11
                    if failure == 'snapshot':
                        raise RuntimeError('snapshot failed')
                    return None if failure == 'empty_snapshot' else 22
                def execute(url, sql):
                    if 'information_schema.tables' in sql:
                        return {'rows': [['t']]}
                    if sql.startswith('SELECT COUNT'):
                        if failure == 'count':
                            raise RuntimeError('count failed')
                        return {'rows': [[100]]}
                    if sql.startswith('SHOW COLUMNS'):
                        raise RuntimeError('columns failed')
                    self.fail('unexpected write: ' + sql)
                with patch.object(b.wr, 'trino', return_value=contextlib.nullcontext('url')), \
                     patch.object(b, 'latest_snapshot', side_effect=latest), \
                     patch.object(b, 'sql', side_effect=execute), \
                     patch.object(b, 'audit_table', return_value={'row_count': 100, 'null_counts': {}}), \
                     patch.object(b, 'save_wap_entry') as save:
                    result = b.data(SimpleNamespace(publish='t'))
                self.assertFalse(result['valid'])
                self.assertIn('publish blocked', result['error'])
                save.assert_not_called()

    def test_publish_and_refresh_pin_snapshot_before_audit(self):
        for action in ('publish', 'refresh'):
            statements = []
            def execute(url, sql):
                statements.append(sql)
                return {'rows': []}
            with self.subTest(action=action), \
                 patch.object(b.wr, 'trino', return_value=contextlib.nullcontext('url')), \
                 patch.object(b, 'sql', side_effect=execute), \
                 patch.object(b, 'latest_snapshot', return_value=11), \
                 patch.object(b, 'audit_table', side_effect=[{'snapshot_id': 11, 'row_count': 2,
                     'null_counts': {}}, {'snapshot_id': 33}]) as audit, \
                 patch.object(b, 'load_wap_registry', return_value={'t': {'sql': 'SELECT 1 AS id'}}), \
                 patch.object(b, 'save_wap_entry'):
                result = b.data(SimpleNamespace(**{action: 't'}))
            self.assertTrue(result['valid'])
            self.assertEqual(audit.call_args_list[0].kwargs, {'snapshot_id': 11})
            self.assertTrue(any(s.endswith('FOR VERSION AS OF 11') and '."marts".' in s for s in statements))
