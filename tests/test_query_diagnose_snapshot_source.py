"""Offline source selection and integrity for snapshot-aware diagnosis."""
from copy import deepcopy
import fcntl
import hashlib
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from tools.local_experiment.query_diagnose import run as R, snapshot_source as SS
from tools.local_experiment.query_snapshots import contract as S, run as SR
from test_query_diagnose_contract import snapshot_source, source
from test_query_diagnose_run import receipt as legacy_receipt


def receipt():
    s, legacy = snapshot_source(), legacy_receipt()
    request = s['request']
    queries = legacy['queries']
    window = {'schema_version':2, 'identity':s['identity'], 'snapshots':s['snapshot_catalog']}
    statements = [S.discovery_sql(request), S.schema_sql(request),
        S.select_sql(request,s['inventory'],s['identity']['columns'],window), S.schema_sql(request)]
    for row, sql in zip(queries['worker'],statements):
        row['sql_sha256'] = hashlib.sha256(sql.encode()).hexdigest()
    queries['worker'][2]['row_count'] = s['selection']['fetched_row_count']
    runtime = legacy['runtime']
    for row in runtime['catalog']['reads']:
        row['request_sha256'] = R.C.digest(S.base_request(request))
    observation = {'state':'verified','error_code':None,'cleanup_verified':True,
        'request_sha256':R.C.digest(request),'inventory':s['inventory'],
        'identity_before':deepcopy(s['identity']),'identity_after':deepcopy(s['identity']),
        'snapshot_catalog_before':deepcopy(s['snapshot_catalog']),'snapshot_catalog_after':deepcopy(s['snapshot_catalog']),
        'selection':s['selection'],'queries':queries,'runtime':runtime,'query_drain':legacy['query_drain']}
    return {'schema_version':2,'evidence_token':s['read_token'],'request':request,
        'request_sha256':R.C.digest(request),'result':'passed','invocations':1,
        'started_at':legacy['started_at'],'finished_at':legacy['finished_at'],
        'observation':observation,'cleanup_verified':True,'error_code':None,
        'source_hashes':{name:'a'*64 for name in SR.sources()},'model_calls':0,'cloud_calls':0,'retries':0,
        'limits':['LOCAL_EXPERIMENT_ONLY']}


class SnapshotSourceTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve(); self.root.chmod(0o700)
        self.old, self.new = self.root/'old', self.root/'new'
        self.old.mkdir(mode=0o700); self.new.mkdir(mode=0o700)
        self.value = receipt(); self.token = self.value['evidence_token']
        self.folder = self.new/self.token; self.folder.mkdir(mode=0o700)
        (self.folder/'run.lock').touch(mode=0o600)
        self.path = self.folder/'summary.json'; self.write()
        for mocked in (patch.object(R,'READ_STATE',self.old),patch.object(R,'SNAPSHOT_READ_STATE',self.new)):
            mocked.start(); self.addCleanup(mocked.stop)

    def write(self):
        self.path.write_bytes(R.C.canonical(self.value)); self.path.chmod(0o600)

    def reject(self):
        with self.assertRaisesRegex(R.DiagnoseError, '^SOURCE_INVALID$'):
            with R.SourceLease(self.token):
                pass

    def test_finished_snapshot_receipt_keeps_current_and_selected_ids_distinct(self):
        raw = self.path.read_bytes(); before = R.fingerprint(self.path.stat())
        with R.SourceLease(self.token) as lease:
            self.assertEqual(lease.source['profile'],'snapshot-reader-v2')
            self.assertNotEqual(lease.source['identity']['current_snapshot_id'],lease.source['selection']['snapshot_id'])
            self.assertEqual(lease.source['summary_sha256'],hashlib.sha256(raw).hexdigest())
            self.assertEqual(lease.source['sql_sha256'],hashlib.sha256(R.D.select_sql(lease.source).encode()).hexdigest())
            lease.unchanged()
        self.assertEqual(self.path.read_bytes(),raw)
        self.assertEqual(R.fingerprint(self.path.stat()),before)

    def test_historical_code_hash_values_are_accepted_but_keyset_is_fixed(self):
        self.value['source_hashes'] = {name:'d'*64 for name in self.value['source_hashes']}; self.write()
        with R.SourceLease(self.token):
            pass
        self.value['source_hashes']['foreign.py'] = 'a'*64; self.write(); self.reject()

    def test_selected_window_query_catalog_and_counts_must_be_consistent(self):
        original = deepcopy(self.value)
        mutations = [lambda v:v.update(model_calls=True), lambda v:v.update(retries=1),
            lambda v:v.update(cleanup_verified=False), lambda v:v.update(result='failed'),
            lambda v:v['observation']['selection'].update(current_snapshot_id=v['request']['snapshot_id']),
            lambda v:v['observation']['selection'].update(snapshot_id=v['observation']['identity_before']['current_snapshot_id']),
            lambda v:v['observation']['selection'].update(rows=[]),
            lambda v:v['observation']['snapshot_catalog_after'].pop(0),
            lambda v:v['observation']['identity_after'].update(metadata_sha256='f'*64),
            lambda v:v['observation']['queries']['worker'][2].update(sql_sha256='f'*64),
            lambda v:v['observation']['queries']['worker'][2].update(row_count=True),
            lambda v:v['observation']['queries']['worker'][2].update(response_complete=False),
            lambda v:v['observation']['runtime']['worker'].update(user='foreign'),
            lambda v:v['observation']['runtime']['catalog']['reads'][0].update(request_sha256='f'*64),
            lambda v:v['observation']['query_drain'].update(active=['unknown']),
            lambda v:v['observation'].update(raw_plan='private')]
        for index, mutate in enumerate(mutations):
            with self.subTest(index=index):
                self.value=deepcopy(original); mutate(self.value); self.write(); self.reject()

    def test_current_reader_still_uses_legacy_schema_and_validator(self):
        self.folder.rename(self.root/'unused')
        folder=self.old/self.token;folder.mkdir(mode=0o700)
        (folder/'run.lock').touch(mode=0o600)
        path=folder/'summary.json';path.write_bytes(R.C.canonical(legacy_receipt()));path.chmod(0o600)
        with R.SourceLease(self.token) as lease:
            self.assertEqual(lease.source['schema_version'],1)
            self.assertNotIn('profile',lease.source)

    def test_same_token_in_both_profiles_refuses_ambiguity_without_fallback(self):
        folder=self.old/self.token;folder.mkdir(mode=0o700)
        (folder/'summary.json').write_text('broken legacy source')
        with patch.object(R,'validate_source',side_effect=AssertionError('not reached')):
            self.reject()

    def test_profile_directory_and_schema_cannot_be_swapped(self):
        self.value=legacy_receipt();self.write();self.reject()
        self.value=receipt();self.write();self.folder.rename(self.old/self.token)
        self.reject()

    def test_busy_snapshot_source_prevents_allocation_and_model_environment(self):
        environment=Mock()
        output=self.root/'diagnosis';output.mkdir(mode=0o700)
        with (self.folder/'run.lock').open('rb') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            owner=R.Owner('e'*32,self.token,output)
            report,public=owner.execute(environment_factory=environment)
        environment.assert_not_called()
        self.assertEqual(report['error_code'],'SOURCE_BUSY')
        self.assertIsNone(public)

    def test_missing_failed_or_ambiguous_source_stops_before_environment(self):
        self.value['result']='failed';self.write()
        environment=Mock();observer=Mock();output=self.root/'diagnosis';output.mkdir(mode=0o700)
        report,public=R.Owner('e'*32,self.token,output).execute(environment_factory=environment,observer=observer)
        self.assertEqual(report['error_code'],'SOURCE_INVALID');self.assertIsNone(public)
        environment.assert_not_called();observer.assert_not_called()

    def test_same_bytes_replaced_file_and_later_profile_collision_are_changes(self):
        with R.SourceLease(self.token) as lease:
            raw=self.path.read_bytes();self.path.unlink();self.path.write_bytes(raw);self.path.chmod(0o600)
            with self.assertRaisesRegex(R.DiagnoseError,'SOURCE_CHANGED'):
                lease.unchanged()
        with R.SourceLease(self.token) as lease:
            (self.old/self.token).mkdir(mode=0o700)
            with self.assertRaisesRegex(R.DiagnoseError,'SOURCE_CHANGED'):
                lease.unchanged()

    def test_private_mode_symlink_and_hardlinked_source_rejected(self):
        self.path.chmod(0o644);self.reject();self.path.chmod(0o600)
        other=self.root/'hardlink';os.link(self.path,other);self.reject();other.unlink()
        raw=self.path.read_bytes();self.path.unlink();other.write_bytes(raw);other.chmod(0o600)
        self.path.symlink_to(other);self.reject()

    def test_unlock_cancel_survives_secondary_close_failure(self):
        cancel=KeyboardInterrupt('original');lease=R.SourceLease(self.token);lease.fd=999
        with patch.object(R.fcntl,'flock',side_effect=cancel),patch.object(R.os,'close',side_effect=OSError('secondary')):
            with self.assertRaises(KeyboardInterrupt) as raised:
                lease.__exit__(None,None,None)
        self.assertIs(raised.exception,cancel);self.assertIsNone(lease.fd)

    def test_source_read_cancel_survives_stream_and_fd_close_failures(self):
        cancel=KeyboardInterrupt('original');close=R.os.close
        class Stream:
            def __init__(self,fd): self.fd=fd
            def read(self,*_): raise cancel
            def close(self):
                close(self.fd)
                raise OSError('stream-close-secondary')
        def final_close(fd):
            close(fd)
            raise OSError('fd-close-secondary')
        with patch.object(R.os,'fdopen',side_effect=lambda fd,*_:Stream(fd)), \
                patch.object(R.os,'close',side_effect=final_close),self.assertRaises(KeyboardInterrupt) as raised:
            R.read_private(self.path)
        self.assertIs(raised.exception,cancel)


if __name__ == '__main__':
    unittest.main()
