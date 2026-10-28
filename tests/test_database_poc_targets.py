"""Focused routing checks; all state, SQL and REST access is mocked."""

import unittest
from unittest.mock import Mock, patch

from tools.local_experiment import database_poc as poc


class DatabasePocTargetsTests(unittest.TestCase):
    def targets(self):
        return {
            db: {'origin': 'http://127.0.0.1:' + str(18181 + i),
                 'realm': db + '-realm', 'catalog': 'service_poc_' + db,
                 'prefix': 's3://service-bucket/warehouse/' + db}
            for i, db in enumerate(poc.DATABASES)
        }

    def run_verify(self, targets=None, *, bad_location=False, own_denied=False):
        routes = targets if targets is not None else {
            db: {'origin': 'http://localhost:18181', 'realm': 'original-realm',
                 'catalog': poc.catalog(db), 'prefix': poc.prefix(db)}
            for db in poc.DATABASES
        }
        credentials = {db: {'mock_principal': db} for db in poc.DATABASES}
        state = {db + '-principal.json': credentials[db] for db in poc.DATABASES}
        state['realm.json'] = {'realm': 'original-realm'}
        calls = []

        def polaris(http, realm, principal):
            db = principal['mock_principal']
            own = routes[db]
            other = routes['finance' if db == 'sales' else 'sales']
            self.assertEqual((http, realm, principal),
                             (own['origin'], own['realm'], credentials[db]))
            own_base = '/api/catalog/v1/' + own['catalog'] + '/namespaces'
            foreign_base = '/api/catalog/v1/' + other['catalog'] + '/namespaces'
            seen_own = False

            def request(method, path):
                nonlocal seen_own
                calls.append((db, http, realm, method, path))
                self.assertEqual(method, 'GET')
                if path == own_base:
                    if own_denied:
                        raise poc.Failure('own unavailable', status=403)
                    seen_own = True
                    return {'namespaces': [[s] for s in poc.SCHEMAS]}
                if path == own_base + '/raw/tables/orders':
                    self.assertTrue(seen_own)
                    return {'metadata': {
                        'table-uuid': '00000000-0000-0000-0000-000000000001',
                        'location': own['prefix'] + ('_sibling' if bad_location else '') + '/orders'}}
                self.assertIn(path, (foreign_base, foreign_base + '/raw/tables/orders'))
                self.assertTrue(seen_own)
                raise poc.Failure('foreign hidden', status=404)

            return Mock(request=Mock(side_effect=request))

        # Unrelated SQL cases fail immediately, including rollback prerequisites.
        # This exercises verify's callbacks without emulating its SQL workload.
        trino = Mock(receipts=[], last={})
        trino.sql.side_effect = poc.Failure('SQL omitted by focused test',
                                           status='PERMISSION_DENIED')
        with patch.object(poc, 'load', side_effect=lambda name: state[name]) as load, \
                patch.object(poc, 'save') as save, \
                patch.object(poc, 'HTTP', side_effect=lambda origin: origin), \
                patch.object(poc, 'Polaris', side_effect=polaris), \
                patch('builtins.print'):
            kwargs = {} if targets is None else {'targets': targets}
            poc.verify(trino, 'http://localhost:18181', 'orders', **kwargs)
        report = save.call_args.args[1]
        self.assertEqual(len(report['checks']), 95)
        return {entry['step']: entry for entry in report['checks']}, calls, trino, load

    def test_default_and_explicit_routes(self):
        for targets in (None, self.targets()):
            with self.subTest(explicit=targets is not None):
                checks, calls, trino, load = self.run_verify(targets)
                statements = [call.args[1] for call in trino.sql.call_args_list]
                for db in poc.DATABASES:
                    own = targets[db] if targets else {
                        'catalog': poc.catalog(db), 'prefix': poc.prefix(db)}
                    for step in ('own_list_load', 'other_list', 'other_load'):
                        self.assertEqual(checks['polaris.' + db + '.' + step]['status'], 'PASSED')
                    self.assertEqual(checks['polaris.' + db + '.own_list_load']['location'],
                                     own['prefix'] + '/orders')
                    self.assertIn('SELECT * FROM ' + own['catalog'] + '.raw.orders', statements)
                    self.assertIn('SELECT * FROM ' + db + '.raw.orders', statements)
                    if targets:
                        self.assertNotIn('SELECT * FROM ' + poc.catalog(db) + '.raw.orders', statements)
                    db_calls = [c for c in calls if c[0] == db]
                    self.assertEqual(len(db_calls), 6)
                    other = 'finance' if db == 'sales' else 'sales'
                    foreign = targets[other]['catalog'] if targets else poc.catalog(other)
                    foreign_base = '/api/catalog/v1/' + foreign + '/namespaces'
                    self.assertEqual([c[-1] for c in db_calls][-3::2],
                                     [foreign_base, foreign_base + '/raw/tables/orders'])
                names = [call.args[0] for call in load.call_args_list]
                self.assertEqual(names.count('realm.json'), 0 if targets else 6)
                for db in poc.DATABASES:
                    self.assertEqual(names.count(db + '-principal.json'), 3)
                self.assertEqual(set(names), {'sales-principal.json', 'finance-principal.json'}
                                 | (set() if targets else {'realm.json'}))

    def test_mapped_prefix_boundary(self):
        checks, _, _, _ = self.run_verify(self.targets(), bad_location=True)
        for db in poc.DATABASES:
            entry = checks['polaris.' + db + '.own_list_load']
            self.assertEqual(entry['status'], 'FAILED')
            self.assertEqual(entry['reason'], 'table location escaped expected prefix')

    def test_cross_requires_own_positive(self):
        checks, calls, _, _ = self.run_verify(self.targets(), own_denied=True)
        for db in poc.DATABASES:
            for step in ('other_list', 'other_load'):
                entry = checks['polaris.' + db + '.' + step]
                self.assertEqual(entry['status'], 'FAILED')
                self.assertIn('own Polaris catalog prerequisite failed', entry['reason'])
            self.assertEqual({c[-1] for c in calls if c[0] == db}, {
                '/api/catalog/v1/service_poc_' + db + '/namespaces'})

    def test_invalid_targets_precede_all_operations(self):
        invalid = [{}, [], {'sales': self.targets()['sales']}]
        extra = self.targets()
        extra['other'] = extra['sales']
        invalid.append(extra)
        for field, value in [('realm', ''), ('realm', ' '), ('realm', None),
                             ('catalog', 1), ('catalog', 'bad.catalog'),
                             ('origin', 'http://example.com:8181'),
                             ('origin', 'http://localhost'),
                             ('origin', 'http://localhost:bad'),
                             ('origin', 'http://localhost:8181/path'),
                             ('origin', 'http://user@localhost:8181'),
                             ('prefix', 'file:///warehouse/sales'),
                             ('prefix', 's3://bucket'),
                             ('prefix', 's3://bucket/path/'),
                             ('prefix', 's3://bucket:bad/path')]:
            target = self.targets()
            target['finance'][field] = value
            invalid.append(target)
        for field in ('origin', 'realm', 'catalog', 'prefix'):
            target = self.targets()
            del target['finance'][field]
            invalid.append(target)
        target = self.targets()
        target['finance']['extra'] = 'unexpected'
        invalid.append(target)
        target = self.targets()
        target['finance'] = None
        invalid.append(target)
        for targets in invalid:
            with self.subTest(targets=targets), \
                    patch.object(poc, 'load') as load, \
                    patch.object(poc, 'save') as save, \
                    patch.object(poc, 'HTTP') as http, \
                    patch.object(poc, 'Polaris') as polaris:
                trino = Mock()
                with self.assertRaises(poc.Failure):
                    poc.verify(trino, 'http://localhost:18181', 'orders', targets=targets)
                self.assertEqual(trino.mock_calls, [])
                for operation in (load, save, http, polaris):
                    operation.assert_not_called()

    def test_validation_copies_valid_mapping(self):
        targets = self.targets()
        self.assertEqual(poc.verification_targets(targets), targets)
        validated = poc.verification_targets(targets)
        validated['sales']['realm'] = 'changed'
        self.assertNotEqual(validated, targets)


if __name__ == '__main__':
    unittest.main()
