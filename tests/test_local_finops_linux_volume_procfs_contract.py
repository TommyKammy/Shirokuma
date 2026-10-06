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
    return {'boot_id':str(uuid.uuid4()),'proc_device':20,'proc_inode':1,
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

    def test_status_and_fdinfo_refuse_raw_controls_in_unrelated_rows(self):
        for field in ('self_status', 'fdinfo'):
            for bad in (b'\x01', b'\x7f'):
                value = inputs()
                value[field] += b'other:\tvalue' + bad + b'\n'
                with self.subTest(field=field, bad=bad), self.assertRaises(P.ProcfsError):
                    P.project(**value)

    def test_status_and_fdinfo_refuse_ambiguous_identity_labels(self):
        for field, additions in (
            ('self_status', (b'NSpid: invalid\n', b'NSpid:\n', b'NSpid:\t101 102\n')),
            ('fdinfo', (b'mnt_id: invalid\n', b'mnt_id:\n', b'mnt_id:\t20\n')),
        ):
            for extra in additions:
                value = inputs()
                value[field] += extra
                with self.subTest(field=field, extra=extra), self.assertRaises(P.ProcfsError):
                    P.project(**value)

    def test_self_namespace_pid_refuses_trailing_padding(self):
        for suffix in (b' ', b'\t', b'\t\t'):
            value = inputs()
            value['self_status'] = value['self_status'].replace(b'NSpid:\t101\n',
                                                                b'NSpid:\t101' + suffix + b'\n')
            with self.subTest(suffix=suffix), self.assertRaises(P.ProcfsError):
                P.project(**value)
        self.assertTrue(P.project(**inputs())['probe']['query_complete'])

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

    def test_boot_identity_path_cannot_be_overmounted(self):
        for mount in (b'/proc/sys', b'/proc/sys/kernel', b'/proc/sys/kernel/random',
                      b'/proc/sys/kernel/random/boot_id'):
            value = inputs()
            value['mountinfo'] += b'20 19 0:21 / ' + mount + b' rw - tmpfs tmpfs rw\n'
            with self.subTest(mount=mount), self.assertRaises(P.ProcfsError):
                P.project(**value)
        value = inputs()
        value['mountinfo'] += b'20 19 0:21 / /proc/acpi rw - tmpfs tmpfs rw\n'
        self.assertTrue(P.project(**value)['probe']['query_complete'])

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

    def test_mount_namespace_root_can_have_zero_parent_id(self):
        value = inputs()
        value['mountinfo'] = value['mountinfo'].replace(b'19 1 0:20', b'19 0 0:20')
        self.assertTrue(P.project(**value)['probe']['query_complete'])
        value['mountinfo'] = value['mountinfo'].replace(b'19 0 0:20', b'19 00 0:20')
        with self.assertRaises(P.ProcfsError):
            P.project(**value)

    def test_proc_mount_device_must_match_pinned_proc_stat_device(self):
        for device, row_device in ((21, b'0:20'), (20, b'0:21'), (20, b'1:20')):
            value = inputs()
            value['proc_device'] = device
            value['mountinfo'] = value['mountinfo'].replace(b'0:20', row_device, 1)
            with self.subTest(device=device, row_device=row_device), self.assertRaises(P.ProcfsError):
                P.project(**value)
        self.assertTrue(P.project(**inputs())['probe']['query_complete'])

    def test_mountinfo_requires_canonical_single_space_columns(self):
        original = inputs()['mountinfo']
        for row in (original.replace(b'19 1 ', b'19  1 ', 1),
                    original.replace(b'19 1 ', b'19\t1 ', 1),
                    original.replace(b' - proc ', b'  - proc ', 1),
                    original.replace(b' - proc ', b' -  proc ', 1),
                    original.replace(b' rw,hidepid=0', b' rw,hidepid=0 ', 1)):
            value = inputs(); value['mountinfo'] = row
            with self.subTest(row=row), self.assertRaises(P.ProcfsError):
                P.project(**value)

    def test_oversized_mount_numbers_fail_with_scoped_error(self):
        original = inputs()['mountinfo']
        for old, new in ((b'19 1 ', b'1' * 5000 + b' 1 '),
                         (b'19 1 ', b'19 ' + b'1' * 5000 + b' '),
                         (b'0:20 ', b'0:' + b'1' * 5000 + b' ')):
            value = inputs()
            value['mountinfo'] = original.replace(old, new, 1)
            with self.subTest(field=old, raw_length=len(new)), self.assertRaises(P.ProcfsError):
                P.project(**value)

    def test_unrelated_mount_rows_do_not_hide_ambiguous_options(self):
        for option in (b'ro,rw', b'rw,rw', b'nosuid', b'rw,'):
            value = inputs()
            value['mountinfo'] += b'20 19 0:21 / /other ' + option + b' - tmpfs tmpfs rw\n'
            with self.subTest(option=option), self.assertRaises(P.ProcfsError):
                P.project(**value)
        value = inputs()
        value['mountinfo'] += b'20 19 0:21 / /other rw - tmpfs tmpfs rw,ro\n'
        with self.assertRaises(P.ProcfsError):
            P.project(**value)

    def test_unrelated_mount_options_refuse_raw_control_bytes(self):
        for options, super_options in ((b'rw,nosuid\x01', b'rw'),
                                       (b'rw', b'rw,mode=700\x7f')):
            value = inputs()
            value['mountinfo'] += (b'20 19 0:21 / /other ' + options +
                                   b' - tmpfs tmpfs ' + super_options + b'\n')
            with self.subTest(options=options, super_options=super_options), \
                    self.assertRaises(P.ProcfsError):
                P.project(**value)

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

    def test_mountinfo_propagation_tags_require_bounded_unique_kinds(self):
        for tags in (b'unknown:1', b'shared:0', b'shared:01',
                     b'shared:9223372036854775808', b'shared:1 shared:2',
                     b'unbindable unbindable'):
            value=inputs()
            value['mountinfo']=value['mountinfo'].replace(b' - proc proc ', b' '+tags+b' - proc proc ')
            with self.subTest(tags=tags),self.assertRaises(P.ProcfsError):
                P.project(**value)
        value=inputs()
        value['mountinfo']=value['mountinfo'].replace(
            b' - proc proc ', b' shared:1 master:2 propagate_from:3 - proc proc ')
        self.assertTrue(P.project(**value)['probe']['query_complete'])

    def test_unbindable_cannot_coexist_with_propagation_ids(self):
        for tags in (b'unbindable shared:1', b'shared:1 unbindable',
                     b'unbindable master:2', b'propagate_from:3 unbindable'):
            value = inputs()
            value['mountinfo'] = value['mountinfo'].replace(
                b' - proc proc ', b' ' + tags + b' - proc proc ')
            with self.subTest(tags=tags), self.assertRaises(P.ProcfsError):
                P.project(**value)
        value = inputs()
        value['mountinfo'] = value['mountinfo'].replace(
            b' - proc proc ', b' unbindable - proc proc ')
        self.assertTrue(P.project(**value)['probe']['query_complete'])

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

    def test_stat_numeric_fields_cannot_be_forged_around_start_tick(self):
        for field, pid in (('self_stat', 101), ('init_stat', 1), ('target_stat', 345)):
            for bad in (b'not-a-number', b'01', b'9223372036854775808'):
                value=inputs()
                value[field]=stat_line(pid,42).replace(b') S 0',b') S '+bad,1)
                if field=='target_stat':value['target_errno']=None
                with self.subTest(field=field,bad=bad),self.assertRaises(P.ProcfsError):
                    P.project(**value)

    def test_kernel_lowercase_process_states_are_visible(self):
        for state in (b't', b'x'):
            value = inputs()
            value['self_stat'] = stat_line(101, 42).replace(b') S ', b') ' + state + b' ', 1)
            with self.subTest(state=state):
                self.assertTrue(P.project(**value)['probe']['query_complete'])
        value = inputs()
        value['self_stat'] = stat_line(101, 42).replace(b') S ', b') q ', 1)
        with self.assertRaises(P.ProcfsError):
            P.project(**value)

    def test_stat_negative_zero_is_not_a_canonical_kernel_number(self):
        for field, pid in (('self_stat', 101), ('init_stat', 1), ('target_stat', 345)):
            value = inputs()
            value[field] = stat_line(pid, 42).replace(b') S 0', b') S -0', 1)
            if field == 'target_stat':
                value['target_errno'] = None
            with self.subTest(field=field), self.assertRaises(P.ProcfsError):
                P.project(**value)

    def test_stat_control_separators_cannot_supply_start_ticks(self):
        for field, pid in (('self_stat', 101), ('init_stat', 1), ('target_stat', 345)):
            for separator in (b'\r', b'\v', b'\f'):
                value=inputs()
                value[field]=stat_line(pid, 42).replace(b') S 0', b') S'+separator+b'0')
                if field=='target_stat':value['target_errno']=None
                with self.subTest(field=field, separator=separator),self.assertRaises(P.ProcfsError):
                    P.project(**value)

    def test_stat_numeric_columns_require_kernel_spacing_and_valid_suffix(self):
        for field, pid in (('self_stat', 101), ('init_stat', 1), ('target_stat', 345)):
            for raw in (stat_line(pid, 42).replace(b') S 0', b') S\t0', 1),
                        stat_line(pid, 42).replace(b') S 0', b') S  0', 1),
                        stat_line(pid, 42) + b' forged',
                        stat_line(pid, 42) + b' 01'):
                value=inputs();value[field]=raw
                if field=='target_stat':value['target_errno']=None
                with self.subTest(field=field,raw=raw),self.assertRaises(P.ProcfsError):
                    P.project(**value)
        value=inputs();value['target_stat']=stat_line(345,42)+b' 0 123\n'
        value['target_errno']=None
        self.assertEqual(P.project(**value)['probe']['start_ticks'],'42')


if __name__=='__main__':unittest.main()
