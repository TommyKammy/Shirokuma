from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from tools.local_experiment.finops import host_storage as H, contract as C, render as HTML, history as HISTORY, run as R
from test_local_finops_contract import NOW
from test_local_finops_process_report import report, processes
from test_local_finops_run import storage_complete, docker_storage_complete, host_storage_complete


def identity(): return dict(owner_sha256='a'*64,directory_identity={'device':'1','inode':'42'})


class HostStorageTests(unittest.TestCase):
    def test_three_fixed_references_not_usage_or_platform_coverage(self):
        backend=Mock();backend.now.return_value=NOW;backend.snapshot.return_value=identity()
        result=H.collect(backend)
        self.assertEqual([c.args[0] for c in backend.snapshot.call_args_list],
                         [x for component in H.TARGETS for x in (component,component)])
        self.assertEqual([x['directory'] for x in result['components']],list(H.TARGETS.values()))
        self.assertTrue(all(x['state']=='observed' and x['used_bytes'] is None and
                            x['usage_reason']=='NOT_MEASURED' for x in result['components']))
        self.assertNotIn(str(H.ROOT),json.dumps(result))

    def test_owner_or_directory_identity_drift_and_invalid_public_shape(self):
        for component in H.TARGETS:
            before=identity();after=deepcopy(before);after['directory_identity']['inode']='43'
            with self.assertRaisesRegex(H.HostStorageError,'IDENTITY_CHANGED'):
                H.project(component,before,after,request_started_at=NOW,observed_at=NOW)
            after=deepcopy(before);after['owner_sha256']='b'*64
            with self.assertRaises(H.HostStorageError):
                H.project(component,before,after,request_started_at=NOW,observed_at=NOW)
        row=H.unknown('mlflow','READ_FAILED');row['directory']='tmp/private/foreign'
        with self.assertRaises(H.HostStorageError):H.validate_row(row)
        row=H.unknown('mlflow','READ_FAILED');row['used_bytes']='0'
        with self.assertRaises(H.HostStorageError):H.validate_row(row)

    def test_directory_symlink_permissions_and_missing_are_unknown_not_zero(self):
        with tempfile.TemporaryDirectory(dir=H.ROOT/'tmp/local-experiment') as temp:
            root=Path(temp); owned=root/'owned'; owned.mkdir(mode=0o700)
            link=root/'alias';link.symlink_to(owned,target_is_directory=True)
            with patch.object(H,'ROOT',root):
                self.assertTrue(H._directory(owned)['inode'].isdecimal())
                with self.assertRaises(H.HostStorageError):H._directory(link)
                owned.chmod(0o755)
                with self.assertRaises(H.HostStorageError):H._directory(owned)
        backend=Mock();backend.now.return_value=NOW;backend.snapshot.side_effect=FileNotFoundError('PRIVATE_PATH')
        rows=H.collect(backend)['components'];self.assertTrue(all(r['state']=='unknown' and
              r['reason']=='READ_FAILED' and r['directory_identity'] is None for r in rows))

    def test_fixed_worker_transport_and_no_lifecycle(self):
        backend=R.Backend()
        with patch.object(backend,'_command',return_value=json.dumps(host_storage_complete())) as call:
            self.assertEqual(backend.host_storage(),host_storage_complete())
            self.assertEqual(call.call_count,1)
            args=call.call_args.args[0]
            self.assertEqual(args[1:3],['-I','-B'])
            self.assertTrue(args[0].endswith('/notebook_lab/.venv/bin/python'))
            self.assertTrue(args[3].endswith('/finops/host_storage.py'))
            self.assertEqual(args[4:],["--measure-serving-home"])
            self.assertNotIn('start',args)

    def test_public_v5_history_and_v4_absence(self):
        old=report(processes())
        v4=C.build_report(old['components'],old['disks'],old['inference'],observed_at=NOW,
                          local_processes=processes(),storage=storage_complete(),
                          docker_storage=docker_storage_complete())
        v5=C.build_report(old['components'],old['disks'],old['inference'],observed_at=NOW,
                          local_processes=processes(),storage=storage_complete(),
                          docker_storage=docker_storage_complete(),host_storage=host_storage_complete())
        self.assertEqual(v5['schema_version'],5);self.assertEqual(C.validate_public(v5),v5)
        self.assertIn('固定ホストディレクトリ参照',HTML.render(v5))
        h=HISTORY.render(HISTORY.parse(json.dumps([v4,v5]).encode()))
        self.assertIn('id="observation-2-host-storage"',h)
        self.assertEqual(h.count('このschemaにはホスト保存先参照は含まれません'),1)
        v5['host_storage']['components'][0]['owner_sha256']='BAD'
        with self.assertRaises((H.HostStorageError,C.FinOpsError)):C.validate_public(v5)


if __name__=='__main__':unittest.main()
