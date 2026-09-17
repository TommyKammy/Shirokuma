"""Retained configuration migration, partial failure and exact reader scope."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


M = load('history_reader_runtime', ROOT / 'tools/local_experiment/grafana/manage.py')
A = load('history_reader_access', ROOT / 'tools/local_experiment/grafana/history_access.py')
C = {'owner_token': 'a' * 32, 'reader_user': 'shirokuma_grafana_ro_' + 'a' * 32, 'reader_password': 'b' * 64}


class HistoryAccessTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.state = Path(temporary.name)
        self.state.chmod(0o700)
        self.old = M.reader_xml(C)
        self.history = M.reader_xml(C, M.HISTORY_READER_TABLES)
        self.current = self.old
        self.writes = []
        self.failure = None
        patches = [patch.object(M, 'STATE', self.state), patch.object(M, 'credentials', return_value=C),
                   patch.object(M, 'agenthouse', return_value={'Id': 'retained-ch'}),
                   patch.object(M.AH, 'Client'), patch.object(A, 'verify_history_tables'),
                   patch.object(M, 'docker', side_effect=self.docker),
                   patch.object(M, 'verify_reader', side_effect=self.verify_reader)]
        self.mocks = []
        for item in patches:
            self.mocks.append(item.start())
            self.addCleanup(item.stop)

    def docker(self, *args, **kwargs):
        if args[-2:] == ('cat', M.CH_CONFIG):
            return self.current
        receipts = list(self.state.glob('reader-scope-*/receipt.json'))
        receipt = json.loads(receipts[-1].read_text())
        self.assertEqual(receipt['pending'], 'replace-exact-owned-reader-config')
        self.assertIn(A.sha(self.current), args[-1])
        self.writes.append(kwargs['input_text'])
        self.current = kwargs['input_text']
        if self.failure:
            raise self.failure
        return ''

    def verify_reader(self, _):
        tables = M.HISTORY_READER_TABLES if self.current == self.history else M.BASE_READER_TABLES
        return {'select_only': True, 'logical_rows': dict.fromkeys(tables, 1)}

    def test_upgrade_is_repeatable_and_preserves_exact_backup(self):
        first = A.change_scope(M, True)
        self.assertTrue(first['changed'])
        self.assertEqual(self.writes, [self.history])
        self.assertEqual((self.state / first['evidence_token'] / 'before.xml').read_text(), self.old)
        second = A.change_scope(M, True)
        self.assertFalse(second['changed'])
        self.assertEqual(len(self.writes), 1)
        self.assertEqual(set(second['reader_permissions']['logical_rows']), set(M.HISTORY_READER_TABLES))

    def test_unknown_existing_xml_refuses_all_mutation_and_preflight(self):
        self.current = self.old.replace('GRANT SELECT', 'GRANT ALL')
        with self.assertRaises(M.GrafanaError):
            A.change_scope(M, True)
        self.assertEqual(self.writes, [])
        self.mocks[4].assert_not_called()
        self.assertEqual(list(self.state.glob('reader-scope-*')), [])

    def test_source_table_rejection_precedes_config_write(self):
        self.mocks[4].side_effect = M.GrafanaError('unknown schema')
        with self.assertRaises(M.GrafanaError):
            A.change_scope(M, True)
        self.assertEqual(self.writes, [])
        self.assertEqual(self.current, self.old)

    def test_uncertain_replace_keeps_journal_and_retry_reconciles_without_second_write(self):
        self.failure = M.GrafanaError('response lost after replacement')
        with self.assertRaises(M.GrafanaError):
            A.change_scope(M, True)
        receipt = json.loads(next(self.state.glob('reader-scope-*/receipt.json')).read_text())
        self.assertEqual((receipt['result'], receipt['pending']), ('failed', 'replace-exact-owned-reader-config'))
        self.assertEqual(self.current, self.history)
        self.failure = None
        self.assertFalse(A.change_scope(M, True)['changed'])
        self.assertEqual(len(self.writes), 1)

    def test_identity_change_blocks_config_write(self):
        self.mocks[2].side_effect = [{'Id': 'retained-ch'}, {'Id': 'replacement-ch'}]
        with self.assertRaises(M.GrafanaError):
            A.change_scope(M, True)
        self.assertEqual(self.writes, [])

    def test_disable_restores_baseline_and_does_not_need_history_tables(self):
        self.current = self.history
        result = A.change_scope(M, False)
        self.assertEqual(self.current, self.old)
        self.assertFalse(result['history_enabled'])
        self.mocks[4].assert_not_called()

    def test_restart_preserves_enabled_scope_without_replacing_config(self):
        M.ensure_reader(C, {'Id': 'retained-ch'})
        self.current = self.history
        M.ensure_reader(C, {'Id': 'retained-ch'})
        self.assertEqual(self.writes, [])
        self.assertEqual((self.state / 'reader-users.xml').read_text(), self.history)


class ReaderScopeTests(unittest.TestCase):
    def test_only_two_complete_select_scopes_are_accepted(self):
        grants = lambda tables: [{'access_type': 'SELECT', 'database': 'agenthouse', 'table': table,
                                 'column': None, 'is_partial_revoke': 0, 'grant_option': 0} for table in tables]
        def query(_, sql):
            if sql == 'SELECT currentUser()':
                return C['reader_user']
            if sql == 'SELECT currentUser(), 1':
                return C['reader_user'] + '\t1'
            if 'FROM system.settings' in sql:
                return json.dumps({'data': [{'name': k, 'value': v} for k, v in M.SETTINGS.items()]})
            if sql in ('SELECT 1 SETTINGS readonly=0', 'SELECT 1 SETTINGS max_execution_time=31', 'SELECT 1 SETTINGS max_result_rows=0'):
                raise M.QueryRejected(500, 452)
            if sql.startswith('CHECK GRANT'):
                return '0'
            return '1'
        candidates = [(grants(M.HISTORY_READER_TABLES), True),
                      (grants(M.HISTORY_READER_TABLES[:-1]), False),
                      (grants(M.HISTORY_READER_TABLES + ('unrelated',)), False),
                      ([{**row, 'grant_option': 1} for row in grants(M.HISTORY_READER_TABLES)], False),
                      (grants(M.HISTORY_READER_TABLES) + grants(('pawprints',)), False)]
        for rows, accepted in candidates:
            admin = Mock()
            admin.query.side_effect = lambda sql: json.dumps({'data': [] if 'role_grants' in sql else rows})
            with self.subTest(accepted=accepted, rows=rows), patch.object(M.AH, 'Client', return_value=admin), \
                    patch.object(M, 'reader_query', side_effect=query):
                if accepted:
                    self.assertEqual(set(M.verify_reader(C)['logical_rows']), set(M.HISTORY_READER_TABLES))
                else:
                    with self.assertRaises(M.GrafanaError):
                        M.verify_reader(C)


if __name__ == '__main__':
    unittest.main()
