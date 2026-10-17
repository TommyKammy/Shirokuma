import contextlib
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools' / 'local_experiment'))
import bears_resident as b


class WapResidentTests(unittest.TestCase):
    def test_history_resolves_rollback_before_newest_commit(self):
        with patch.object(b, 'sql', return_value={'rows': [[11]]}) as sql:
            self.assertEqual(b.latest_snapshot('url', 'iceberg.marts.t'), 11)
        sql.assert_called_once_with('url', 'SELECT snapshot_id FROM "iceberg"."marts"."t$history" '
                                    'ORDER BY made_current_at DESC LIMIT 1')

    def test_snapshot_fallback_for_missing_or_empty_history(self):
        for history in (RuntimeError('unsupported'), {'rows': []}):
            with self.subTest(history=history), patch.object(b, 'sql', side_effect=[history, {'rows': [[22]]}]) as sql:
                self.assertEqual(b.latest_snapshot('url', 'iceberg.marts.t'), 22)
                self.assertIn('"t$snapshots" ORDER BY committed_at DESC LIMIT 1', sql.call_args.args[1])

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
