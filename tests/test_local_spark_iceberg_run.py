import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from tools.local_experiment.spark_iceberg import run as R

TOKEN = '1'*32
BUILD = '2'*32
IMAGE = 'sha256:'+'a'*64
CID = 'c'*64


def container():
    return {'Id': CID, 'Name': '/'+R.name(TOKEN), 'Image': IMAGE,
            'Config': {'Labels': {R.LABEL: R.OWNER, 'shirokuma.io/run-token': TOKEN},
                       'Hostname': 'localhost', 'User': '185:185', 'Entrypoint': ['/opt/spark/bin/spark-submit'],
                       'Cmd': R.C.spark_arguments(TOKEN),
                       'Env': ['HOME=/tmp', 'TMPDIR=/tmp', 'SPARK_LOCAL_IP=127.0.0.1', 'PYTHONDONTWRITEBYTECODE=1']},
            'HostConfig': {'NetworkMode': 'none', 'Privileged': False, 'ReadonlyRootfs': True,
                           'CapDrop': ['ALL'], 'CapAdd': None, 'SecurityOpt': ['no-new-privileges'],
                           'NanoCpus': 1_000_000_000, 'Memory': 2*1024**3, 'MemorySwap': 2*1024**3,
                           'PidsLimit': 256, 'Binds': None, 'Tmpfs': dict(R.TMPFS), 'AutoRemove': False,
                           'RestartPolicy': {'Name': 'no'}}, 'Mounts': [],
            'State': {'Status': 'created', 'Running': False, 'ExitCode': 0, 'OOMKilled': False, 'Error': ''}}


class FakeDocker:
    def __init__(self):
        self.value = None
        self.calls = []
        self.runtime = {'daemon': 'original'}
        self.complete = True
        self.exit_code = 0
        self.create_error = None
        self.start_error = None
        self.log_error = None
        self.mutate_start = None

    def guard(self):
        self.calls.append('guard')
        return dict(self.runtime)

    def find(self, *, container_id=None, run_token=None):
        self.calls.append('find')
        if self.value and self.value['State']['Running'] and self.complete:
            self.value['State'].update(Status='exited', Running=False, ExitCode=self.exit_code)
        return copy.deepcopy(self.value)

    def create(self, run_token, image):
        self.calls.append('create'); self.value = container()
        if self.create_error:
            raise self.create_error
        return CID

    def start(self, container_id):
        self.calls.append('start')
        if self.start_error:
            raise self.start_error
        self.value['State'].update(Status='running', Running=True)
        if self.mutate_start:
            self.mutate_start(self)

    def logs(self, container_id, output):
        self.calls.append('logs')
        if self.log_error:
            error, self.log_error = self.log_error, None
            raise error
        R.save(output/'docker-logs.json', {'stdout': 'bounded output', 'stderr': ''})
        return 'bounded output'

    def stop(self, container_id):
        self.calls.append('stop'); self.value['State'].update(Running=False, Status='exited')

    def remove(self, container_id):
        self.calls.append('remove'); self.value = None


class RunnerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.parent = Path(self.tmp.name).resolve()
        self.output = self.parent/TOKEN
        self.output.mkdir(mode=0o700)
        self.docker = FakeDocker()
        self.parser = mock.patch.object(R.C, 'parse_output', return_value={'result': 'passed'}).start()
        self.addCleanup(mock.patch.stopall)

    def instance(self, **kwargs):
        return R.Experiment(TOKEN, IMAGE, BUILD, self.output, docker=self.docker, sleep=lambda _: None, **kwargs)

    def test_success_has_fixed_container_identity_and_owned_cleanup(self):
        receipt = self.instance().run()
        self.assertEqual(receipt['result'], 'passed')
        self.assertTrue(receipt['cleanup']['verified'])
        self.assertTrue(receipt['cleanup']['removed'])
        self.assertEqual(receipt['container_id'], CID)
        self.assertTrue((self.output/'final-container.json').exists())
        self.assertEqual(self.docker.calls.count('create'), 1)

    def test_nonzero_exit_cannot_pass_even_with_correct_json(self):
        self.docker.exit_code = 1
        with self.assertRaisesRegex(Exception, 'WORKLOAD_EXIT_FAILURE'):
            self.instance().run()
        self.assertIn('remove', self.docker.calls)
        self.assertTrue((self.output/'docker-logs.json').exists())

    def test_failure_payload_cannot_pass_even_with_exit_zero(self):
        self.parser.return_value = {'result': 'failed'}
        with self.assertRaisesRegex(Exception, 'WORKLOAD_RESULT_FAILURE'):
            self.instance().run()
        self.assertIn('remove', self.docker.calls)

    def test_parser_failure_is_preserved_and_container_removed(self):
        self.parser.side_effect = ValueError('BAD_OUTPUT')
        with self.assertRaisesRegex(ValueError, 'BAD_OUTPUT'):
            self.instance().run()
        self.assertIn('remove', self.docker.calls)

    def test_timeout_stops_only_known_container_and_keeps_log(self):
        self.docker.complete = False
        clock = iter([0, 181]).__next__
        with self.assertRaisesRegex(Exception, 'WORKLOAD_TIMEOUT'):
            self.instance(clock=clock).run()
        self.assertEqual([x for x in self.docker.calls if x in ('stop', 'remove')], ['stop', 'remove'])

    def test_unknown_create_has_durable_intent_and_never_adopts_by_name(self):
        self.docker.create_error = TimeoutError()
        with self.assertRaises(TimeoutError):
            self.instance().run()
        saved = json.loads((self.output/'summary.json').read_text())
        self.assertTrue(saved['creation_attempted'])
        self.assertIsNone(saved['container_id'])
        self.assertEqual(saved['pending'], 'create')
        self.assertEqual(saved['cleanup']['deferred'], 'CONTAINER_CREATE_OUTCOME_UNKNOWN')
        self.assertNotIn('remove', self.docker.calls)
        self.assertNotIn('stop', self.docker.calls)

    def test_name_collision_never_creates_or_removes(self):
        self.docker.value = container()
        with self.assertRaisesRegex(Exception, 'CONTAINER_NAME_EXISTS'):
            self.instance().run()
        self.assertNotIn('create', self.docker.calls)
        self.assertNotIn('remove', self.docker.calls)

    def test_start_failure_cleans_recorded_id(self):
        self.docker.start_error = TimeoutError()
        with self.assertRaises(TimeoutError):
            self.instance().run()
        self.assertIn('remove', self.docker.calls)

    def test_changed_owner_defers_cleanup_without_signalling(self):
        self.docker.mutate_start = lambda d: d.value['Config']['Labels'].update({R.LABEL: 'foreign'})
        with self.assertRaisesRegex(Exception, 'CONTAINER_OWNER_CHANGED'):
            self.instance().run()
        self.assertNotIn('remove', self.docker.calls)
        self.assertNotIn('stop', self.docker.calls)

    def test_changed_daemon_defers_cleanup(self):
        self.docker.mutate_start = lambda d: d.runtime.update(daemon='other')
        with self.assertRaisesRegex(Exception, 'CLEANUP_NOT_VERIFIED'):
            self.instance().run()
        self.assertNotIn('remove', self.docker.calls)

    def test_original_cancellation_survives_cleanup_failure(self):
        error = KeyboardInterrupt()
        self.docker.log_error = error
        self.docker.remove = mock.Mock(side_effect=ValueError('SECONDARY'))
        with self.assertRaises(KeyboardInterrupt) as raised:
            self.instance().run()
        self.assertIs(raised.exception, error)

    def test_recover_completed_run_is_noop_and_preserves_original(self):
        self.instance().run()
        original = (self.output/'summary.json').read_bytes()
        self.docker.calls.clear()
        with mock.patch.object(R, 'STATE', self.parent):
            recovered = R.recover(TOKEN, docker=self.docker)
        self.assertTrue(recovered['cleanup']['already_absent'])
        self.assertNotIn('create', self.docker.calls)
        self.assertNotIn('start', self.docker.calls)
        self.assertNotIn('remove', self.docker.calls)
        self.assertEqual((self.output/'summary.json').read_bytes(), original)

    def test_foreign_identity_or_unsafe_container_is_rejected(self):
        changes = [('Image', IMAGE.replace('a', 'b')), ('Id', 'd'*64)]
        for key, value in changes:
            changed = container(); changed[key] = value
            with self.assertRaises(Exception):
                R.validate_container(changed, TOKEN, IMAGE, CID)
        for key, value in [('NetworkMode', 'host'), ('Privileged', True), ('ReadonlyRootfs', False),
                           ('Memory', 0), ('PidsLimit', 0), ('Binds', ['/host:/host']),
                           ('PortBindings', {'8888/tcp': []}), ('Tmpfs', {}), ('CapAdd', ['SYS_ADMIN'])]:
            with self.subTest(key=key):
                changed = container(); changed['HostConfig'][key] = value
                with self.assertRaises(Exception):
                    R.validate_container(changed, TOKEN, IMAGE, CID)

    def test_command_has_no_host_mount_ports_catalog_or_mutable_image(self):
        args = R.create_arguments(TOKEN, IMAGE)
        self.assertEqual(args[args.index('--network')+1], 'none')
        self.assertFalse(set(args) & {'--volume', '-v', '--mount', '-p', '--publish', '--privileged'})
        self.assertIn(IMAGE, args)
        for bad in ('latest', 'spark:4.1.3', IMAGE+'extra'):
            with self.assertRaises(Exception):
                R.create_arguments(TOKEN, bad)

    def test_real_log_reader_decodes_saved_command_json(self):
        engine = R.Docker()
        def command(*args, **kwargs):
            R.save(kwargs['log_path'], {'exit_code': 0, 'stdout': 'result-json', 'stderr': 'spark-diagnostics'})
            return 'result-json'
        with mock.patch.object(engine, 'call', side_effect=command):
            self.assertEqual(engine.logs(CID, self.output), 'result-json\nspark-diagnostics')

    def test_native_jni_uses_explicit_exec_tmpfs_and_loopback_hostname(self):
        args = R.create_arguments(TOKEN, IMAGE)
        self.assertEqual(args[args.index('--hostname')+1], 'localhost')
        self.assertIn('exec', R.TMPFS['/tmp'].split(','))
        self.assertIn('noexec', R.TMPFS['/opt/spark/work-dir'].split(','))
        changed = container(); changed['Config']['Hostname'] = 'unresolved'
        with self.assertRaisesRegex(Exception, 'CONTAINER_COMMAND_CHANGED'):
            R.validate_container(changed, TOKEN, IMAGE, CID)


if __name__ == '__main__':
    unittest.main()
