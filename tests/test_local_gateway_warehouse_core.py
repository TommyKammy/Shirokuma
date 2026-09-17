"""Safety and evidence tests for the isolated Gateway fixture and PG restore."""
import base64
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.local_experiment.gateway_warehouse import build, fixtures, manifest as M, persistence as P
from tools.local_experiment.gateway_warehouse.support import ExperimentError, digest

TOKEN = '1234567890abcdef1234567890abcdef'
UID = str(uuid.uuid4())


def live(expected):
    obj = copy.deepcopy(expected)
    obj['metadata'].update(uid=UID, resourceVersion='42')
    return obj


def snapshot():
    columns = [[table, 'field' + str(i), 'text', 'YES', None]
               for table, count in zip(P.TABLES, (10, 5, 8)) for i in range(count)]
    return {'version': '18.4', 'user': fixtures.DATABASE_USER, 'tables': P.TABLES, 'columns': columns,
            'indexes': [['table', 'index' + str(i), 'fixed'] for i in range(5)],
            'backends': sorted([{'name': row['name'], 'routing_group': row['routingGroup'], 'backend_url': row['proxyTo'],
                          'external_url': row['externalUrl'], 'active': True}
                         for row in fixtures.backend_records(TOKEN)], key=lambda row: row['name']),
            'migrations': [{'version': str(i), 'type': 'SQL', 'success': True, 'checksum': i}
                           for i in range(1, 5)], 'history_count': 0}


class CoreTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.output = Path(self.temporary.name).resolve()
        self.material = fixtures.create_fixtures(TOKEN, self.output)
        self.images = copy.deepcopy(build.PINS['images'])
        self.objects = M.render(self.images, TOKEN, self.material)
        self.plan = {'run_token': TOKEN, 'images': self.images, 'objects': self.objects}

    def tearDown(self):
        self.temporary.cleanup()

    def test_private_material_and_no_secret_receipt(self):
        secret = self.material['secret_data']['postgres-password']
        self.assertEqual(len(secret), 64)
        receipt = (self.output / 'fixture-receipt.json').read_text()
        self.assertNotIn(secret, receipt)
        self.assertEqual((self.output / 'fixture-receipt.json').stat().st_mode & 0o777, 0o600)
        self.assertIn(secret, self.material['redaction_values'])
        self.assertFalse(json.loads(self.material['secret_data']['gateway-config.yaml'])['dataStore']['queryHistoryEnabled'])

    def test_four_owned_objects_no_retained_connections(self):
        self.assertEqual(M.validate_plan(self.plan), self.objects)
        self.assertEqual([x['kind'] for x in self.objects], ['NetworkPolicy', 'ConfigMap', 'Secret', 'Pod'])
        for obj in self.objects:
            M.assert_owned(live(obj), obj, UID)
        spec = self.objects[-1]['spec']
        self.assertEqual([c['name'] for c in spec['containers']], ['xs', 's', 'postgres', 'gateway'])
        self.assertEqual(self.objects[0]['spec']['egress'], [])
        self.assertFalse(spec['automountServiceAccountToken'])
        self.assertTrue(all(c['securityContext']['readOnlyRootFilesystem'] for c in spec['containers']))
        self.assertTrue(all('emptyDir' in v or 'configMap' in v or 'projected' in v for v in spec['volumes']))
        self.assertNotIn('persistentVolumeClaim', json.dumps(spec))
        self.assertNotIn('iceberg', json.dumps(self.material))

    def test_separate_nodes_resources_and_readonly(self):
        xs, s = [fixtures.trino_config(TOKEN, size) for size in ('xs', 's')]
        self.assertNotEqual(xs['node.properties'], s['node.properties'])
        self.assertIn('-Xmx1G', xs['jvm.config'])
        self.assertIn('-Xmx2G', s['jvm.config'])
        for data in (xs, s):
            self.assertEqual(data['readonly.properties'], 'access-control.name=read-only\n')
            self.assertIn('http-server.process-forwarded=true', data['config.properties'])
            self.assertNotIn('opa.', json.dumps(data))

    def test_renderer_rejects_extra_catalog_and_remote_database(self):
        for change in ('catalog', 'database'):
            bad = copy.deepcopy(self.material)
            if change == 'catalog':
                bad['public_data']['xs.iceberg.properties'] = 'connector.name=iceberg'
            else:
                data = json.loads(bad['secret_data']['gateway-config.yaml'])
                data['dataStore']['jdbcUrl'] = 'jdbc:postgresql://retained:5432/retained'
                bad['secret_data']['gateway-config.yaml'] = json.dumps(data)
            with self.assertRaises(ExperimentError):
                M.render(self.images, TOKEN, bad)

    def test_plan_hash_and_saved_recovery_do_not_follow_new_image_pins(self):
        bad = copy.deepcopy(self.plan)
        bad['objects'][1]['data']['routing-rules.yaml'] += '\n'
        with self.assertRaisesRegex(ExperimentError, 'HASH'):
            M.validate_plan(bad)
        with patch.dict(build.PINS['images'], {'gateway': 'trinodb/trino-gateway@sha256:' + '0' * 64}):
            self.assertEqual(M.validate_plan(self.plan), self.objects)
        with patch.object(M, 'pod_spec', side_effect=AssertionError('current renderer must not run')):
            self.assertEqual(M.validate_plan(self.plan), self.objects)
        with patch.object(M, 'GATEWAY_START', 'changed startup'), patch.dict(M.RESOURCES, {'gateway': {}}):
            self.assertEqual(M.validate_plan(self.plan), self.objects)

    def test_saved_plan_scope_rejects_resealed_host_or_namespace(self):
        for field in ('host', 'namespace', 'image', 'command'):
            bad = copy.deepcopy(self.plan)
            obj = bad['objects'][-1]
            if field == 'host':
                obj['spec']['hostNetwork'] = True
            elif field == 'namespace':
                obj['metadata']['namespace'] = 'default'
            elif field == 'image':
                bad['images']['trino'] = 'different/image@sha256:' + '0' * 64
            else:
                obj['spec']['containers'][0]['command'] = ['sh', '-c', 'echo unexpected']
            raw = copy.deepcopy(obj)
            raw['metadata'].pop('annotations')
            obj['metadata']['annotations'][M.HASH] = digest(raw)
            with self.assertRaises(ExperimentError):
                M.validate_plan(bad)

    def test_same_name_different_uid_or_drift_rejected(self):
        expected = self.objects[-1]
        current = live(expected)
        with self.assertRaises(ExperimentError):
            M.assert_owned(current, expected, str(uuid.uuid4()))
        current['spec']['containers'][0]['image'] = 'unexpected'
        with self.assertRaises(ExperimentError):
            M.assert_owned(current, expected, UID)

    def test_kubernetes_probe_defaults_are_safe(self):
        expected, current = self.objects[-1], live(self.objects[-1])
        for container in current['spec']['containers']:
            for port in container.get('ports', []):
                port['protocol'] = 'TCP'
            for key in ('startupProbe', 'readinessProbe'):
                probe = container[key]
                probe.update(initialDelaySeconds=0, timeoutSeconds=1, successThreshold=1)
                probe.setdefault('failureThreshold', 3)
                if 'httpGet' in probe:
                    probe['httpGet']['scheme'] = 'HTTP'
        M.assert_owned(current, expected, UID)

    def test_fixed_route_header_and_empty_default(self):
        config = json.loads(self.material['secret_data']['gateway-config.yaml'])
        self.assertEqual(config['routing']['defaultRoutingGroup'], 'local-unrouted')
        records = fixtures.backend_records(TOKEN)
        self.assertEqual({r['routingGroup'] for r in records}, {'local-xs', 'local-s'})
        rules = self.material['public_data']['routing-rules.yaml']
        self.assertIn('X-Shirokuma-Warehouse', rules)
        self.assertNotIn('X-Trino-Routing-Group', rules)

    def test_build_requires_arm64_digest_and_owned_trino(self):
        image = self.images['trino']
        valid = {'Architecture': 'arm64', 'Os': 'linux', 'Id': 'sha256:' + 'a' * 64, 'Size': 200,
                 'RepoDigests': [image], 'Config': {'Labels': {'shirokuma.dev.component': 'trino-opa-shadow'}}}
        with patch.object(build, 'docker', return_value=json.dumps([valid])):
            self.assertEqual(build.inspect_image(image, 'trino')['image'], image)
        for key, value in [('Architecture', 'amd64'), ('RepoDigests', []), ('Config', {})]:
            bad = copy.deepcopy(valid)
            bad[key] = value
            with patch.object(build, 'docker', return_value=json.dumps([bad])):
                with self.assertRaises(ExperimentError):
                    build.inspect_image(image, 'trino')

    def pg_backend(self, *, collision=False, drift=False, fail_restore=False):
        calls = []
        count = {'snapshots': 0}

        def execute(args, **kwargs):
            calls.append((args, kwargs))
            if 'psql' in args:
                if args[-1] == P.COLLISION_SQL:
                    return json.dumps({'exists': collision}).encode()
                value = snapshot()
                count['snapshots'] += 1
                if drift and count['snapshots'] == 3:
                    value['migrations'][0]['checksum'] += 1
                return json.dumps(value).encode()
            if 'pg_dump' in args:
                return b'PGDMP' + b'fixed synthetic fixture'
            if 'pg_restore' in args and fail_restore:
                raise ExperimentError('PG_COMMAND_FAILED')
            return b''
        return calls, execute

    def verify_pg(self, execute):
        guard = lambda: live(self.objects[-1])
        with patch.object(P, 'binary_command', side_effect=execute):
            return P.verify_backup_restore(TOKEN, self.objects[-1], UID, self.output, guard=guard)

    def test_restore_is_new_db_only_dump_private_source_unchanged(self):
        calls, execute = self.pg_backend()
        result = self.verify_pg(execute)
        self.assertEqual(result['result'], 'passed')
        self.assertTrue(result['source_unchanged'])
        self.assertEqual(result['history_rows'], 0)
        dump = self.output / 'gateway-postgresql.dump'
        self.assertEqual(dump.stat().st_mode & 0o777, 0o600)
        restored = [args for args, _ in calls if 'pg_restore' in args]
        self.assertEqual(len(restored), 1)
        self.assertEqual(restored[0][restored[0].index('-d') + 1], 'vw_restored')
        self.assertNotIn('--clean', restored[0])
        self.assertTrue(all(args[args.index('-c') + 1] == 'postgres' for args, _ in calls))
        self.assertEqual(result['dump']['sha256'], digest(dump.read_bytes()))

    def test_restore_collision_stops_before_dump_or_create(self):
        calls, execute = self.pg_backend(collision=True)
        with self.assertRaisesRegex(ExperimentError, 'COLLISION'):
            self.verify_pg(execute)
        self.assertFalse(any('createdb' in args or 'pg_dump' in args for args, _ in calls))
        self.assertEqual(json.loads((self.output / 'database.json').read_text())['result'], 'failed')

    def test_source_drift_and_partial_restore_fail_honestly(self):
        calls, execute = self.pg_backend(fail_restore=True)
        with self.assertRaises(ExperimentError):
            self.verify_pg(execute)
        result = json.loads((self.output / 'database.json').read_text())
        self.assertEqual(result['pending'], 'restore')
        self.assertTrue(result['restore_created'])
        self.assertFalse(result['source_unchanged'])
        self.assertEqual(result['result'], 'failed')
        self.assertTrue((self.output / 'gateway-postgresql.dump').exists())

    def test_source_changes_after_dump_are_not_reported_as_preserved(self):
        _, execute = self.pg_backend(drift=True)
        with self.assertRaisesRegex(ExperimentError, 'PG_RESTORE_MISMATCH'):
            self.verify_pg(execute)
        value = json.loads((self.output / 'database.json').read_text())
        self.assertEqual(value['result'], 'failed')
        self.assertFalse(value['source_unchanged'])
        self.assertEqual(value['pending'], 'readback')

    def test_interrupt_keeps_pending_restore_and_private_dump(self):
        _, execute = self.pg_backend()
        def interrupted(args, **kwargs):
            if 'pg_restore' in args:
                raise KeyboardInterrupt()
            return execute(args, **kwargs)
        with self.assertRaises(KeyboardInterrupt):
            self.verify_pg(interrupted)
        value = json.loads((self.output / 'database.json').read_text())
        self.assertEqual(value['result'], 'failed')
        self.assertEqual(value['error_type'], 'KeyboardInterrupt')
        self.assertEqual(value['pending'], 'restore')
        self.assertTrue(value['restore_created'])
        self.assertIsNone(value['restored'])

    def test_guard_replacement_stops_before_any_pg_exec(self):
        bad = live(self.objects[-1])
        bad['metadata']['uid'] = str(uuid.uuid4())
        with patch.object(P, 'binary_command') as invoke:
            with self.assertRaises(ExperimentError):
                P.verify_backup_restore(TOKEN, self.objects[-1], UID, self.output, guard=lambda: bad)
        invoke.assert_not_called()

    def test_guard_runs_for_every_command(self):
        calls, execute = self.pg_backend()
        guard_calls = []
        def guard():
            guard_calls.append(len(calls))
            return live(self.objects[-1])
        with patch.object(P, 'binary_command', side_effect=execute):
            P.verify_backup_restore(TOKEN, self.objects[-1], UID, self.output, guard=guard)
        self.assertEqual(guard_calls, list(range(len(calls))))

    def test_snapshot_rejects_history_or_missing_migration(self):
        for change in ('history', 'migrations', 'backend'):
            value = snapshot()
            if change == 'history':
                value['history_count'] = 1
            elif change == 'migrations':
                value['migrations'].pop()
            else:
                value['backends'][0]['backend_url'] = 'http://retained:8080'
            with self.assertRaises(ExperimentError):
                P.validate_snapshot(value, TOKEN)

    def test_binary_command_limits_while_reading_and_redacts_stderr(self):
        self.assertEqual(P.binary_command([sys.executable, '-c', 'import sys;sys.stdout.buffer.write(b"PGDMP")']), b'PGDMP')
        with self.assertRaisesRegex(ExperimentError, '^PG_OUTPUT_TOO_LARGE$'):
            P.binary_command([sys.executable, '-c', 'print("x"*100000)'], limit=100)
        with self.assertRaisesRegex(ExperimentError, '^PG_COMMAND_FAILED$'):
            P.binary_command([sys.executable, '-c', 'import sys;print("secret",file=sys.stderr);sys.exit(1)'])

    def test_binary_command_timeout_reaps(self):
        with self.assertRaisesRegex(ExperimentError, '^PG_COMMAND_TIMEOUT$'):
            P.binary_command([sys.executable, '-c', 'import time;time.sleep(10)'], timeout=0.05)


if __name__ == '__main__':
    unittest.main()
