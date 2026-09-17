"""Fake SDK plus stdlib only; never import MLflow or touch a real restore."""
from copy import deepcopy
import io
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch

from tools.local_experiment.mlflow import restore_readback as B
from tests.test_local_mlflow_restore import fixture, refresh

R = B.R


class FakeSDK:
    def __init__(self, expected):
        self.expected = deepcopy(expected)
        self.calls = []
        self.kwargs = None
    def factory(self, **kwargs):
        self.kwargs = kwargs
        return self
    def search_experiments(self, **kwargs):
        self.calls.append(('experiments', kwargs))
        return [NS(experiment_id=key) for key in self.expected['experiments']]
    def search_runs(self, experiment_ids, **kwargs):
        self.calls.append(('runs', kwargs))
        return [NS(info=NS(run_id=key)) for key in self.expected['runs']]
    def get_experiment(self, key):
        return NS(**self.expected['experiments'][key])
    def get_run(self, key):
        value = self.expected['runs'][key]
        return NS(info=NS(**value['info']), data=NS(params=value['params'], tags=value['tags'],
            metrics={k: float('nan') if v == 'nan' else float.fromhex(v) for k, v in value['metrics'].items()}))
    def get_metric_history(self, key, metric):
        self.calls.append(('history', key, metric))
        return [NS(key=k, timestamp=t, step=s, value=float('nan') if v == 'nan' else float.fromhex(v))
                for k, t, s, v in self.expected['runs'][key]['histories'][metric]]
    def list_artifacts(self, key, prefix):
        self.calls.append(('list', key, prefix))
        return [NS(path=path, is_dir=row['is_dir'], file_size=row['size'])
                for path, row in self.expected['runs'][key]['artifacts'].items()
                if (str(Path(path).parent) if '/' in path else None) == prefix]
    def download_artifacts(self, key, path, dst_path=None):
        assert dst_path is None
        assert path == ''  # Nonempty paths use MLflow's copying runs:/ adapter.
        self.calls.append(('download', key, path))
        return str(B.uri_path(self.expected['runs'][key]['info']['artifact_uri'], self.base) / path)


class ReadbackTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.manifest, self.backup = fixture(self.root)
        # Add public SDK fields absent from the intentionally smaller restore fixture.
        with sqlite3.connect(self.backup / 'store/tracking.sqlite') as db:
            run_columns={row[1] for row in db.execute('PRAGMA table_info(runs)')}
            for field, spec in [('name', "TEXT DEFAULT 'fixture'"), ('user_id', "TEXT DEFAULT 'tester'"),
                                ('start_time', 'INTEGER DEFAULT 1700000000000'), ('end_time', 'INTEGER DEFAULT 1700000000010')]:
                if field not in run_columns:
                    db.execute('ALTER TABLE runs ADD COLUMN ' + field + ' ' + spec)
            db.execute('ALTER TABLE metrics ADD COLUMN is_nan INTEGER DEFAULT 0')
            db.execute('ALTER TABLE latest_metrics ADD COLUMN is_nan INTEGER DEFAULT 0')
        refresh(self.root, self.backup, self.manifest)
        self.restored = R.restore(self.manifest, root=self.root)
        self.token = self.restored['token']
        self.checked = R.inspect_completed(self.token, root=self.root)
        self.expected = B.expected_view(self.checked)
        self.sdk = FakeSDK(self.expected)
        self.sdk.base = self.checked['clone_artifacts']

    def sdk_read(self):
        return B.sdk_read(self.checked, self.expected, factory=self.sdk.factory)

    def runner(self, evidence, checked):
        result = B.sdk_read(checked, B.expected_view(checked), factory=self.sdk.factory)
        R.write(evidence / 'worker-result.json', R.canonical(result))

    def readback(self, runner=None):
        return B.readback(self.token, root=self.root, runner=runner or self.runner)

    def latest_result(self):
        return json.loads(next((self.checked['root'] / 'readback').glob('*/result.json')).read_bytes())

    def test_real_restore_to_fake_sdk_preserves_every_table_and_retained_file(self):
        before = B.seal(self.checked)
        result = self.readback()
        self.assertEqual(result['result'], 'passed')
        self.assertEqual(result['counts'], dict(experiments=3, runs=2, metrics=4, artifacts=2))
        self.assertTrue(result['logical_unchanged'] and result['retained_unchanged'])
        self.assertEqual(B.seal(self.checked), before)
        self.assertEqual(len([c for c in self.sdk.calls if c[0] == 'history']), 2)
        uri = 'sqlite:///' + str(self.checked['clone_db'])
        self.assertEqual(self.sdk.kwargs, dict(tracking_uri=uri, registry_uri=uri, workspace_store_uri=uri))
        R.inspect_completed(self.token, root=self.root)  # readback evidence does not invalidate the restore

    def test_full_histories_reject_missing_point_wrong_value_and_boolean_step(self):
        actual = self.sdk.get_metric_history
        for mutation in ('missing', 'value', 'bool'):
            def changed(*args):
                points = actual(*args)
                if mutation == 'missing': return points[:-1]
                if mutation == 'value': points[0].value += 1
                if mutation == 'bool': points[0].step = False
                return points
            with self.subTest(mutation=mutation), patch.object(self.sdk, 'get_metric_history', side_effect=changed):
                with self.assertRaisesRegex(B.ReadbackError, 'HISTORY_MISMATCH'): self.sdk_read()

    def test_sdk_run_params_tags_and_latest_values_are_bound(self):
        key = next(iter(self.sdk.expected['runs']))
        for field in ('params', 'tags', 'metrics'):
            original = deepcopy(self.sdk.expected['runs'][key][field])
            self.sdk.expected['runs'][key][field]['extra'] = '0x1.0p+0' if field == 'metrics' else 'altered'
            with self.subTest(field=field), self.assertRaisesRegex(B.ReadbackError, 'DATA_MISMATCH'):
                self.sdk_read()
            self.sdk.expected['runs'][key][field] = original

    def test_extra_run_and_incomplete_page_are_rejected(self):
        with patch.object(self.sdk, 'search_runs', return_value=[]):
            with self.assertRaisesRegex(B.ReadbackError, 'RUN_MISMATCH'): self.sdk_read()
        class Page(list): token = 'continuation'
        with patch.object(self.sdk, 'search_runs', return_value=Page(self.sdk.search_runs([]))):
            with self.assertRaisesRegex(B.ReadbackError, 'RUN_MISMATCH'): self.sdk_read()

    def test_external_sdk_artifact_uri_is_refused_before_artifact_access(self):
        key = next(iter(self.sdk.expected['runs']))
        self.sdk.expected['runs'][key]['info']['artifact_uri'] = 'https://example.invalid/private'
        with self.assertRaises(B.ReadbackError): self.sdk_read()
        self.assertFalse(any(x[0] in ('list', 'download') for x in self.sdk.calls))

    def test_download_return_path_cannot_escape_even_if_bytes_would_match(self):
        with patch.object(self.sdk, 'download_artifacts', return_value=str(self.backup / 'store.json')):
            with self.assertRaisesRegex(B.ReadbackError, 'ARTIFACT_REFUSED'): self.sdk_read()

    def test_inventory_duplicates_and_unexpected_paths_are_refused_before_download(self):
        actual = self.sdk.list_artifacts
        for wrong in ('duplicate', 'unexpected'):
            def listing(*args):
                entries = actual(*args)
                return entries + entries if wrong == 'duplicate' else [NS(path='unexpected.json', is_dir=False, file_size=0)]
            with self.subTest(wrong=wrong), patch.object(self.sdk, 'list_artifacts', side_effect=listing):
                with self.assertRaises(B.ReadbackError): self.sdk_read()

    def test_artifact_uri_canonical_boundary(self):
        base = self.checked['clone_artifacts']
        for uri in (base.as_uri() + '/%2e%2e/outside', 'file://localhost' + str(base), base.as_uri()+'?q=1',
                    base.as_uri()+'#part', self.backup.as_uri(), 's3://bucket/artifact'):
            with self.subTest(uri=uri), self.assertRaises(B.ReadbackError): B.uri_path(uri, base)

    def test_environment_has_only_clone_uris_and_private_home_tmp(self):
        evidence = self.checked['root'] / 'readback' / ('c' * 32)
        with patch.dict(os.environ, {'AWS_SECRET_ACCESS_KEY':'private', 'HTTPS_PROXY':'http://proxy',
                                     'MLFLOW_TRACKING_URI':'http://127.0.0.1:18550'}):
            env = B.environment(evidence, self.checked['clone_db'], self.checked['clone_artifacts'])
        self.assertNotIn('AWS_SECRET_ACCESS_KEY', env)
        self.assertNotIn('HTTPS_PROXY', env)
        self.assertEqual(env['MLFLOW_DISABLE_TELEMETRY'], 'true')
        self.assertEqual(env['DO_NOT_TRACK'], 'true')
        self.assertEqual(env['TMPDIR'], str(evidence / 'tmp'))
        self.assertEqual(env['_MLFLOW_SERVER_ARTIFACT_ROOT'], self.checked['clone_artifacts'].as_uri())
        self.assertNotIn('18550', repr(env))

    def test_guard_refuses_network_inert_export_and_global_write(self):
        evidence = self.root / 'evidence'; evidence.mkdir(mode=0o700)
        guard = B.audit_guard(self.checked['root'] / 'clone', evidence)
        guard('open', (str(evidence / 'allowed'), 'w', os.O_WRONLY | os.O_CREAT))
        guard('open', (str(self.backup / 'store.json'), 'r', os.O_RDONLY))
        for event, args in [('socket.connect', (None, ('127.0.0.1',18550))),
                            ('open',(str(self.checked['root']/'export/new'), 'w', os.O_WRONLY)),
                            ('os.mkdir',(str(self.root/'outside'),0o700,-1)),
                            ('os.rename',(str(evidence/'a'),str(self.backup/'b'),-1,-1)),
                            ('os.symlink',('outside',str(evidence/'a'),-1)),
                            ('subprocess.Popen',('anything',[],None,None))]:
            with self.subTest(event=event), self.assertRaises(B.ReadbackError): guard(event,args)

    def test_installed_audit_hook_allows_private_stdlib_write_and_blocks_outside(self):
        evidence = self.root/'evidence'; evidence.mkdir(mode=0o700)
        code = '''import runpy,sys,pathlib,os
m=runpy.run_path(sys.argv[1]); e=pathlib.Path(sys.argv[2]); outside=pathlib.Path(sys.argv[3])
sys.addaudithook(m['audit_guard'](e,e))
m['R'].write(e/'ok.json',b'{}')
try: outside.write_bytes(b'forbidden')
except m['ReadbackError']: pass
else: raise AssertionError('outside allowed')
print('passed')
'''
        result = subprocess.run([sys.executable,'-I','-B','-c',code,str(Path(B.__file__)),str(evidence),str(self.root/'outside')],
                                capture_output=True,text=True,timeout=15)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual((evidence/'ok.json').read_bytes(),b'{}')
        self.assertFalse((self.root/'outside').exists())

    def test_launch_is_fixed_isolated_and_reaped_before_post_snapshot(self):
        evidence = self.root/'evidence'; evidence.mkdir(mode=0o700)
        child=Mock(pid=1234,returncode=0)
        with patch.object(B.subprocess,'Popen',return_value=child) as run:
            B.launch(evidence,self.checked)
        args,kwargs=run.call_args
        self.assertEqual(args[0][:3],[str(B.PYTHON),'-I','-B'])
        self.assertEqual(kwargs['cwd'],evidence)
        self.assertTrue(kwargs['start_new_session'])
        child.wait.assert_called_once_with(timeout=120)
        child.kill.assert_not_called()
        self.assertTrue((evidence/'worker-exited.json').exists())
        self.assertEqual(kwargs['stdin'],subprocess.DEVNULL)
        self.assertEqual(kwargs['umask'],0o077)

    def test_sqlite_audit_event_blocks_nonclone_database_in_actual_stdlib_child(self):
        evidence=self.root/'evidence';evidence.mkdir(mode=0o700)
        outside=self.checked['root']/'export/unexpected.sqlite'
        code='''import runpy,sys,pathlib,sqlite3
m=runpy.run_path(sys.argv[1]);clone=pathlib.Path(sys.argv[2]);e=pathlib.Path(sys.argv[3])
sys.addaudithook(m['audit_guard'](clone,e))
c=sqlite3.connect(str(clone/'store/tracking.sqlite'));assert c.execute('SELECT count(*) FROM runs').fetchone()==(2,);c.close()
c=sqlite3.connect(':memory:');c.close()
try: sqlite3.connect(sys.argv[4])
except m['ReadbackError']: pass
else: raise AssertionError('external SQLite accepted')
print('passed')
'''
        result=subprocess.run([sys.executable,'-I','-B','-c',code,str(Path(B.__file__)),str(self.checked['root']/'clone'),str(evidence),str(outside)],
                              capture_output=True,text=True,timeout=15)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertFalse(outside.exists())

    def test_launch_first_cancel_survives_kill_wait_errors_and_never_claims_unreaped(self):
        original=B.ReadbackInterrupted(signal_number:=2)
        for reaps in (True,False):
            evidence=self.root/('reaped' if reaps else 'unknown');evidence.mkdir(mode=0o700)
            child=Mock(pid=1234,returncode=-9)
            child.kill.side_effect=OSError('secondary kill')
            child.wait.side_effect=[original,OSError('secondary wait'),0 if reaps else OSError('second wait')]
            with patch.object(B.subprocess,'Popen',return_value=child):
                with self.assertRaises(B.ReadbackInterrupted) as found:B.launch(evidence,self.checked)
            self.assertIs(found.exception,original)
            self.assertEqual(found.exception.signum,signal_number)
            self.assertEqual(child.wait.call_count,3)
            self.assertEqual((evidence/'worker-exited.json').exists(),reaps)

    def test_post_worker_logical_mutation_refuses_success(self):
        def tamper(evidence,checked):
            self.runner(evidence,checked)
            with sqlite3.connect(checked['clone_db']) as db:db.execute('DELETE FROM metrics')
        with self.assertRaises(B.ReadbackError): self.readback(tamper)
        value=self.latest_result()
        self.assertEqual(value['result'],'failed')
        self.assertFalse(value['logical_unchanged'])
        self.assertTrue(value['retained_unchanged'])

    def test_post_worker_artifact_mutation_refuses_success(self):
        def tamper(evidence,checked):
            self.runner(evidence,checked)
            next(checked['clone_artifacts'].rglob('safe-projection.json')).write_bytes(b'{}')
        with self.assertRaises(B.ReadbackError): self.readback(tamper)
        self.assertFalse(self.latest_result()['retained_unchanged'])

    def test_worker_failure_is_not_retried_or_converted_to_passed(self):
        calls=[]
        def fail(*args):calls.append(1);raise OSError('private failure')
        with self.assertRaisesRegex(B.ReadbackError,'READBACK_FAILED'):self.readback(fail)
        self.assertEqual(calls,[1])
        value=self.latest_result()
        self.assertEqual(value['result'],'failed')
        self.assertIsNone(value['sdk_view_sha256'])

    def test_worker_receipt_cannot_forge_counts_or_add_fields(self):
        def tamper(evidence,checked):
            self.runner(evidence,checked)
            p=evidence/'worker-result.json'; value=json.loads(p.read_bytes());value['counts']['runs']=16
            p.write_bytes(R.canonical(value))
        with self.assertRaises(B.ReadbackError):self.readback(tamper)
        self.assertEqual(self.latest_result()['result'],'failed')

    def test_first_cancel_survives_postcheck_and_final_save_errors(self):
        original=KeyboardInterrupt('original')
        write=R.write
        snapshot=R.logical_snapshot
        cancelled=[]
        def broken(path,raw):
            if path.name=='result.json':raise OSError('secondary')
            return write(path,raw)
        def cancel(*args):cancelled.append(True);raise original
        def post(path):
            if cancelled:raise OSError('post')
            return snapshot(path)
        with patch.object(R,'logical_snapshot',side_effect=post),patch.object(R,'write',side_effect=broken):
            with self.assertRaises(KeyboardInterrupt) as found:self.readback(cancel)
        self.assertIs(found.exception,original)

    def test_invalid_token_rejected_without_inspection_or_allocation(self):
        with patch.object(R,'inspect_completed') as inspect:
            for token in ('A'*32,'../escape','a'*31,None):
                with self.subTest(token=token),self.assertRaises(B.ReadbackError):B.readback(token,root=self.root)
            inspect.assert_not_called()
        self.assertFalse((self.checked['root']/'readback').exists())

    def test_module_import_and_cli_fake_failure_never_import_sdk(self):
        code="import runpy,sys; runpy.run_path(sys.argv[1]); print('mlflow' in sys.modules)"
        result=subprocess.run([sys.executable,'-I','-B','-c',code,str(Path(B.__file__))],capture_output=True,text=True,timeout=15)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(result.stdout.strip(),'False')
        output=io.StringIO()
        with patch.object(B,'readback',side_effect=OSError('private text')),patch('sys.stdout',output):
            self.assertEqual(B.main(['--restore-token',self.token]),1)
        self.assertNotIn('private text',output.getvalue())


if __name__=='__main__':unittest.main()
