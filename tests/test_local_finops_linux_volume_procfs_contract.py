from copy import deepcopy
import errno
import unittest
import uuid

from tools.local_experiment.finops import linux_volume_procfs_contract as P
from tools.local_experiment.finops import linux_volume_receipt_wire as W
from test_local_finops_linux_volume_receipt_wire import envelope, NONCE, OPERATION, DIGEST


def stat_line(pid,start,comm=b'fixture worker'):
    fields=[b'S']+[b'0']*18+[str(start).encode()]
    return str(pid).encode()+b' ('+comm+b') '+b' '.join(fields)


def inputs():
    return {'boot_id':str(uuid.uuid4()),'proc_device':32,'proc_inode':1,
            'proc_mount_id':19,
            'mountinfo':b'19 1 0:20 / /proc rw,nosuid,nodev,noexec - proc proc rw,hidepid=0\n',
            'fdinfo':b'pos:\t0\nflags:\t0100000\nmnt_id:\t19\n',
            'self_pid':101,'self_stat':stat_line(101,100),
            'init_stat':stat_line(1,1),'self_status':b'Name:\tfixture\nNSpid:\t101\n',
            'pid_namespace_link':'pid:[4026531836]',
            'init_pid_namespace_link':'pid:[4026531836]',
            'mount_namespace_link':'mnt:[4026531841]',
            'target_pid':345,'target_stat':None,'target_errno':errno.ENOENT}


class ProcfsContractTests(unittest.TestCase):
    def test_complete_visible_fixture_projects_wire_shape_not_authentication(self):
        value=P.project(**inputs())
        self.assertIsNone(value['probe']['start_ticks'])
        self.assertTrue(value['probe']['query_complete'])
        source=inputs();source['target_stat']=stat_line(345,123456,comm=b'worker ) impostor')
        source['target_errno']=None
        self.assertEqual(P.project(**source)['probe']['start_ticks'],'123456')
        sample=envelope();sample['vm_before']=deepcopy(value['vm'])
        sample['vm_after']=deepcopy(value['vm']);sample['probe']=value['probe']
        sample['record']['boot_id']=value['vm']['boot_id']
        import json
        self.assertEqual(W.parse(json.dumps(sample).encode(),nonce=NONCE,
                        operation_id=OPERATION,expected_bundle_digest=DIGEST,
                        vm_pin=value['vm'])['probe'],value['probe'])

    def test_missing_pid_requires_exact_enoent_and_visible_self_init(self):
        changes=[lambda x:x.update(target_errno=errno.EACCES),
                 lambda x:x.update(target_errno=None),
                 lambda x:x.update(self_stat=None),
                 lambda x:x.update(init_stat=stat_line(2,1)),
                 lambda x:x.update(self_status=b'NSpid:\t99 101\n'),
                 lambda x:x.update(self_status=b'NSpid:\t101\nNSpid:\t101\n'),
                 lambda x:x.update(init_pid_namespace_link='pid:[4026539999]'),
                 lambda x:x.update(target_stat=stat_line(345,123456)),
                 lambda x:x.update(target_pid=101)]
        for change in changes:
            value=inputs();change(value)
            with self.subTest(change=change),self.assertRaises(P.ProcfsError):P.project(**value)

    def test_status_and_fdinfo_require_complete_lf_records(self):
        for field in ('self_status', 'fdinfo'):
            for change in (lambda raw: raw[:-1],
                            lambda raw: raw.replace(b'\n', b'\r\n'),
                            lambda raw: raw + b'other:\x00bad\n',
                            lambda raw: raw.replace(b'\n', b'\v', 1) + b'\n',
                            lambda raw: raw + b'other:\fvalue\n'):
                value=inputs();value[field]=change(value[field])
                with self.subTest(field=field, raw=value[field]),self.assertRaises(P.ProcfsError):
                    P.project(**value)

    def test_wrong_mount_hidden_proc_and_redirected_pid_paths_rejected(self):
        changes=[lambda x:x.update(proc_mount_id=20),
                 lambda x:x.update(fdinfo=b'mnt_id:\t19\nmnt_id:\t19\n'),
                 lambda x:x.update(mountinfo=b'19 1 0:20 /foo /proc rw - proc proc rw\n'),
                 lambda x:x.update(mountinfo=b'19 1 0:20 / /proc rw - tmpfs proc rw\n'),
                 lambda x:x.update(mountinfo=b'19 1 0:20 / /proc rw - proc proc rw,hidepid=2\n'),
                 lambda x:x.update(mountinfo=x['mountinfo']+b'20 19 0:21 / /proc/345 rw - tmpfs tmpfs rw\n'),
                 lambda x:x.update(mountinfo=x['mountinfo']+b'20 19 0:21 / /proc/self/fd rw - tmpfs tmpfs rw\n'),
                  lambda x:x.update(mountinfo=x['mountinfo']+x['mountinfo']),
                  lambda x:x.update(mountinfo=x['mountinfo'].rstrip(b'\n')),
                  lambda x:x.update(mountinfo=x['mountinfo'].replace(b'\n',b'\r\n')),
                  lambda x:x.update(mountinfo=x['mountinfo'].replace(b'\n',b'\xc2\x85')),
                  lambda x:x.update(mount_namespace_link='mnt:[0]'),
                 lambda x:x.update(pid_namespace_link='pid:[0123]')]
        for change in changes:
            value=inputs();change(value)
            with self.subTest(change=change),self.assertRaises(P.ProcfsError):P.project(**value)

    def test_mountpoint_aliases_cannot_hide_pid_overmount(self):
        for mount in (b'/proc/./345', b'/proc//345', b'/proc/x/../345',
                      b'//proc/345', b'/proc/\\999345'):
            value=inputs()
            value['mountinfo']+=b'20 19 0:21 / '+mount+b' rw - tmpfs tmpfs rw\n'
            with self.subTest(mount=mount),self.assertRaises(P.ProcfsError):
                P.project(**value)

    def test_mountinfo_root_parent_and_device_are_structural(self):
        for row in (b'19 -1 0:20 / /proc rw,nosuid,nodev,noexec - proc proc rw,hidepid=0\n',
                    b'19 1 broken / /proc rw,nosuid,nodev,noexec - proc proc rw,hidepid=0\n',
                    b'19 1 0:20 relative /proc rw,nosuid,nodev,noexec - proc proc rw,hidepid=0\n',
                    b'19 1 0:20 /./ /proc rw,nosuid,nodev,noexec - proc proc rw,hidepid=0\n',
                    b'19 1 0:20 /\\999 /proc rw,nosuid,nodev,noexec - proc proc rw,hidepid=0\n',
                    b'19 9223372036854775808 0:20 / /proc rw,nosuid,nodev,noexec - proc proc rw,hidepid=0\n',
                    b'19 1 0:9223372036854775808 / /proc rw,nosuid,nodev,noexec - proc proc rw,hidepid=0\n'):
            value=inputs();value['mountinfo']=row
            with self.subTest(row=row),self.assertRaises(P.ProcfsError):P.project(**value)
        value=inputs()
        value['mountinfo']+=b'20 19 0:21 /name\\040with\\040spaces /other rw - tmpfs tmpfs rw\n'
        self.assertTrue(P.project(**value)['probe']['query_complete'])

    def test_procfs_super_options_do_not_project_ambiguous_access_or_visibility(self):
        for options in (b'rw,ro,hidepid=0', b'rw,rw,hidepid=0',
                        b'rw,hidepid=0,hidepid=0', b'hidepid=0',
                        b'rw,hidepid=0,', b'rw,hidepid=0,gid=0,gid=0'):
            value=inputs()
            value['mountinfo']=value['mountinfo'].replace(b'proc proc rw,hidepid=0',
                                                        b'proc proc '+options)
            with self.subTest(options=options),self.assertRaises(P.ProcfsError):
                P.project(**value)

    def test_procfs_mount_options_do_not_project_ambiguous_access_or_visibility(self):
        for options in (b'rw,ro,nosuid', b'rw,rw,nosuid', b'ro,rw,nosuid',
                        b'nosuid,nodev', b'rw,nosuid,nosuid',
                        b'rw,hidepid=2', b'rw,hidepid=0,hidepid=0'):
            value=inputs()
            value['mountinfo']=value['mountinfo'].replace(
                b'/proc rw,nosuid,nodev,noexec -', b'/proc '+options+b' -')
            with self.subTest(options=options),self.assertRaises(P.ProcfsError):
                P.project(**value)

    def test_forged_stat_field_noncanonical_numbers_and_limits_rejected(self):
        changes=[lambda x:x.update(target_stat=stat_line(999,4),target_errno=None),
                 lambda x:x.update(target_stat=stat_line(345,'0123'),target_errno=None),
                 lambda x:x.update(target_stat=b'345 (broken) S 0',target_errno=None),
                 lambda x:x.update(target_stat=b'x'*4097,target_errno=None),
                 lambda x:x.update(proc_device=True),
                 lambda x:x.update(proc_inode=2**64),
                 lambda x:x.update(mountinfo=b'x'*(P.MAX_MOUNTINFO+1)),
                 lambda x:x.update(boot_id='first-contact'),
                 lambda x:x.update(target_errno=True)]
        for change in changes:
            value=inputs();change(value)
            with self.subTest(change=change),self.assertRaises(P.ProcfsError):P.project(**value)

    def test_stat_control_separators_cannot_supply_start_ticks(self):
        for field, pid in (('self_stat', 101), ('init_stat', 1), ('target_stat', 345)):
            for separator in (b'\r', b'\v', b'\f'):
                value=inputs()
                value[field]=stat_line(pid, 42).replace(b') S 0', b') S'+separator+b'0')
                if field=='target_stat':value['target_errno']=None
                with self.subTest(field=field, separator=separator),self.assertRaises(P.ProcfsError):
                    P.project(**value)


if __name__=='__main__':unittest.main()
