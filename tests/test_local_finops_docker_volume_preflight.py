from copy import deepcopy
import unittest

from tools.local_experiment.finops import docker_volume_preflight as V
from test_local_finops_docker_storage import fixture


def snapshot():
    context, containers, volumes = fixture()
    container=containers[0];container['Image']='sha256:'+'b'*64
    container['State'].update(Status='exited',Restarting=False,
        Paused=False,Dead=False,StartedAt='2026-09-20T00:00:00Z',
        FinishedAt='2026-09-20T00:00:10Z')
    container['RestartCount']=0
    container['HostConfig']={'RestartPolicy':{'Name':'no'}}
    volume=volumes[0];volume['Options']=None
    other={'Id':'c'*64,'State':{'Status':'exited','Running':False,'Restarting':False,
            'Paused':False,'Dead':False,
           'StartedAt':'2026-09-20T00:00:00Z','FinishedAt':'2026-09-20T00:00:10Z'},
           'RestartCount':0,'Mounts':[{'Type':'volume','Name':'unrelated','Driver':'local',
           'Source':'/var/lib/docker/volumes/unrelated/_data','Destination':'/data','RW':True}]}
    return dict(context=context[0],daemon={'ID':'DAEMON:ONE','OSType':'linux',
        'Architecture':'aarch64','DockerRootDir':'/var/lib/docker'},container=container,
        volume=volume,listed_ids=[container['Id'],other['Id']],containers=[container,other],
        boot_id='01234567-89ab-cdef-0123-456789abcdef',
        started_at='2026-09-27T00:00:00Z',observed_at='2026-09-27T00:00:00Z',
        listing_complete=True)


def validate(before, after):
    return V.validate_pair(before,after,now='2026-09-27T00:00:10Z')


class VolumePreflightTests(unittest.TestCase):
    def test_context_socket_requires_one_clean_user_component(self):
        original = snapshot()['context']['Endpoints']['docker']['Host']
        for host in ('unix:///Users//.colima/mac-studio-solo/docker.sock',
                     'unix:///Users/other/nested/.colima/mac-studio-solo/docker.sock',
                     'unix:///Users/other\n/.colima/mac-studio-solo/docker.sock',
                     'unix:///Users/other\\.colima/mac-studio-solo/docker.sock'):
            value = snapshot()
            value['context']['Endpoints']['docker']['Host'] = host
            with self.subTest(host=repr(host)), self.assertRaisesRegex(V.PreflightError, 'SCOPE_INVALID'):
                validate(value, deepcopy(value))
        self.assertEqual(validate(snapshot(), deepcopy(snapshot()))['state'], 'metadata_eligible')

    def test_same_destination_with_different_mount_sources_is_ambiguous(self):
        value = snapshot()
        other = value['containers'][1]
        conflicting = deepcopy(other['Mounts'][0])
        conflicting['Source'] = '/var/lib/docker/volumes/second/_data'
        conflicting['Name'] = 'second'
        other['Mounts'].append(conflicting)
        with self.assertRaisesRegex(V.PreflightError, 'SCOPE_INVALID'):
            validate(value, deepcopy(value))

    def test_stopped_fixed_volume_is_metadata_only_not_scan_permission(self):
        value=snapshot();result=validate(value,deepcopy(value))
        self.assertEqual(result,{'state':'metadata_eligible','container_count':2,
                                 'vm_host_writers_unresolved':True,'volume_traversal_authorized':False})
        self.assertNotIn('/var/lib/docker',repr(result))
        value['volume']['Options']={}
        self.assertEqual(validate(value,deepcopy(value)),result)

    def test_volume_created_after_metadata_window_cannot_be_eligible(self):
        value=snapshot();value['volume']['CreatedAt']='2026-09-28T00:00:00Z'
        with self.assertRaisesRegex(V.PreflightError,'SCOPE_INVALID'):
            validate(value,deepcopy(value))

    def test_wrong_daemon_container_and_driver_options_refused(self):
        cases=[lambda s:s['context']['Endpoints']['docker'].update(Host='tcp://elsewhere'),
               lambda s:s['daemon'].update(Architecture='amd64'),
               lambda s:s['daemon'].update(DockerRootDir='/other'),
               lambda s:s['container']['State'].update(Status='running',Running=True),
               lambda s:s['container']['State'].update(Restarting=True),
               lambda s:s['container']['HostConfig']['RestartPolicy'].update(Name='always'),
               lambda s:s['container']['Config'].update(Image='other'),
               lambda s:s['container']['Mounts'][0].update(Source='/var/lib/docker/volumes/foreign/_data'),
               lambda s:s['volume'].update(Mountpoint='/var/lib/docker/volumes/foreign/_data'),
               lambda s:s['volume'].update(Options={'type':'none','o':'bind'}),
               lambda s:s['volume'].update(Driver='local-foreign'),
               lambda s:s['volume']['Labels'].update({V.AH.LABEL:'other'})]
        for change in cases:
            value=snapshot();change(value)
            with self.subTest(change=change),self.assertRaises(V.PreflightError):
                validate(value,deepcopy(value))

    def test_target_cannot_be_paused_dead_or_ambiguously_stopped(self):
        for flag in ('Paused', 'Dead'):
            for replacement in (True, None, 'false'):
                value = snapshot()
                value['container']['State'][flag] = replacement
                with self.subTest(flag=flag, replacement=replacement), self.assertRaises(V.PreflightError):
                    validate(value, deepcopy(value))
            value = snapshot()
            del value['container']['State'][flag]
            with self.subTest(missing=flag), self.assertRaises(V.PreflightError):
                validate(value, deepcopy(value))

    def test_malformed_nested_metadata_fails_scoped(self):
        changes = [
            lambda s: s['context'].update(Endpoints=None),
            lambda s: s['context'].update(Endpoints={'docker': []}),
            lambda s: s['volume'].update(Labels=[]),
            lambda s: s['container'].update(Config=None),
            lambda s: s['container']['Config'].update(Labels=[]),
            lambda s: s['container'].update(HostConfig=[]),
            lambda s: s['container']['HostConfig'].update(RestartPolicy=None),
            lambda s: s['volume'].update(Options=[]),
            lambda s: s['containers'][1].update(Id=[]),
        ]
        for change in changes:
            value = snapshot()
            change(value)
            with self.subTest(change=change), self.assertRaisesRegex(V.PreflightError, 'SCOPE_INVALID'):
                validate(value, deepcopy(value))

    def test_boot_identity_requires_canonical_uuid_in_each_snapshot(self):
        for boot in ('0' * 36, '01234567-89ab-cdef-0123-456789abcde-',
                     '01234567-89ab-cdef-0123-456789abcdef\n',
                     '01234567-89ab-cdef-0123-456789abcdeG'):
            value = snapshot()
            value['boot_id'] = boot
            with self.subTest(boot=boot), self.assertRaisesRegex(V.PreflightError, 'SCOPE_INVALID'):
                validate(value, deepcopy(value))

    def test_invalid_snapshot_times_fail_as_scoped_preflight_errors(self):
        for key in ('started_at', 'observed_at'):
            for bad in (None, [], 'not-a-timestamp', '2026-09-27T00:00:00Z\n'):
                value = snapshot()
                value[key] = bad
                with self.subTest(key=key, bad=bad), self.assertRaisesRegex(
                        V.PreflightError, 'SCOPE_INVALID'):
                    validate(value, deepcopy(value))
        for bad in (None, [], 'not-a-timestamp', '2026-09-20T00:00:00Z\n'):
            value = snapshot()
            value['volume']['CreatedAt'] = bad
            with self.subTest(created=bad), self.assertRaisesRegex(
                    V.PreflightError, 'SCOPE_INVALID'):
                validate(value, deepcopy(value))
        value = snapshot()
        with self.assertRaisesRegex(V.PreflightError, 'SCOPE_INVALID'):
            V.validate_pair(value, deepcopy(value), now=None)
        with self.assertRaisesRegex(V.PreflightError, 'SCOPE_INVALID'):
            V.validate_pair(value, deepcopy(value), now='2026-09-27T00:00:10Z\n')

    def test_other_container_stop_flags_must_be_present_and_stable(self):
        for flag in ('Paused', 'Dead'):
            first = snapshot()
            second = deepcopy(first)
            second['containers'][1]['State'][flag] = True
            second['containers'][1]['State']['Status'] = flag.lower()
            if flag == 'Paused':
                second['containers'][1]['State']['Running'] = True
            with self.subTest(changed=flag), self.assertRaisesRegex(V.PreflightError, 'IDENTITY_CHANGED'):
                validate(first, second)
            for replacement in (None, 'false'):
                value = snapshot()
                value['containers'][1]['State'][flag] = replacement
                with self.subTest(flag=flag, replacement=replacement), self.assertRaises(V.PreflightError):
                    validate(value, deepcopy(value))
            value = snapshot()
            del value['containers'][1]['State'][flag]
            with self.subTest(missing=flag), self.assertRaises(V.PreflightError):
                validate(value, deepcopy(value))

    def test_unrelated_container_status_cannot_contradict_stop_flags(self):
        for status, flags in (
            ('running', {'Running': False}),
            ('exited', {'Running': True}),
            ('exited', {'Paused': True}),
            ('exited', {'Dead': True}),
            ('created', {'Restarting': True}),
        ):
            value = snapshot()
            value['containers'][1]['State'].update(Status=status, **flags)
            with self.subTest(status=status, flags=flags), self.assertRaisesRegex(
                    V.PreflightError, 'SCOPE_INVALID'):
                validate(value, deepcopy(value))

    def test_unrelated_transient_status_matches_its_flag(self):
        for status, flag in (('paused', 'Paused'), ('restarting', 'Restarting'), ('dead', 'Dead')):
            for set_status in (False, True):
                value = snapshot()
                state = value['containers'][1]['State']
                if set_status:
                    state['Status'] = status
                else:
                    state[flag] = True
                with self.subTest(status=status, set_status=set_status), self.assertRaisesRegex(
                        V.PreflightError, 'SCOPE_INVALID'):
                    validate(value, deepcopy(value))

    def test_all_containers_not_only_owned_label_and_bind_overlaps(self):
        for source in (V.VOLUME_ROOT, '/var/lib/docker',V.VOLUME_ROOT+'/nested'):
            value=snapshot();value['containers'][1]['Mounts']=[{'Type':'bind','Source':source,
                                                                  'Destination':'/data','RW':True}]
            with self.subTest(source=source),self.assertRaisesRegex(V.PreflightError,'SHARED_REFERENCE'):
                validate(value,deepcopy(value))
        value=snapshot();value['containers'][1]['Mounts']=[{'Type':'volume','Name':V.AH.VOLUME,
                                                             'Driver':'local','Source':V.VOLUME_ROOT,
                                                             'Destination':'/data','RW':True}]
        with self.assertRaisesRegex(V.PreflightError,'SHARED_REFERENCE'):
            validate(value,deepcopy(value))
        value=snapshot();value['containers'][1]['Mounts']=[{'Type':'bind','Source':'/var/lib/docker/volumes/other/_data',
                                                             'Destination':'/data','RW':True}]
        self.assertEqual(validate(value,deepcopy(value))['state'],'metadata_eligible')

    def test_unrelated_mount_paths_with_controls_are_not_inventory_evidence(self):
        for field in ('Source', 'Destination'):
            for character in ('\n', '\r', '\t', '\x00', '\x7f', '\x85', '\ud800'):
                value = snapshot()
                value['containers'][1]['Mounts'][0][field] += character + 'alias'
                with self.subTest(field=field, character=repr(character)), self.assertRaisesRegex(
                        V.PreflightError, 'SCOPE_INVALID'):
                    validate(value, deepcopy(value))

    def test_unrelated_volume_name_and_driver_must_be_encodable_clean_text(self):
        for field in ('Name', 'Driver'):
            for character in ('\n', '\x00', '\x7f', '\x85', '\ud800'):
                value = snapshot()
                value['containers'][1]['Mounts'][0][field] += character
                with self.subTest(field=field, character=repr(character)), self.assertRaisesRegex(
                        V.PreflightError, 'SCOPE_INVALID'):
                    validate(value, deepcopy(value))

    def test_all_container_lifecycle_timestamps_are_valid(self):
        for index in (0, 1):
            for field in ('StartedAt', 'FinishedAt'):
                for bad in ('not-a-timestamp', '2026-09-20T00:00:00Z\n'):
                    value = snapshot()
                    value['containers'][index]['State'][field] = bad
                    with self.subTest(index=index, field=field, bad=bad), self.assertRaisesRegex(
                            V.PreflightError, 'SCOPE_INVALID'):
                        validate(value, deepcopy(value))

    def test_duplicate_unrelated_mount_cannot_lend_stable_inventory(self):
        value = snapshot()
        value['containers'][1]['Mounts'].append(deepcopy(value['containers'][1]['Mounts'][0]))
        with self.assertRaisesRegex(V.PreflightError, 'SCOPE_INVALID'):
            validate(value, deepcopy(value))

    def test_incomplete_or_changed_snapshots_rejected(self):
        changes=[lambda s:s['listed_ids'].pop(),
                 lambda s:s['containers'].pop(),
                 lambda s:s['listed_ids'].append(s['listed_ids'][0]),
                 lambda s:s['listed_ids'].extend(['d'*64]*V.MAX_CONTAINERS),
                 lambda s:s['containers'][1].pop('Mounts'),
                 lambda s:s['container']['State'].update(StartedAt='2026-09-27T00:00:02Z'),
                 lambda s:s['container'].update(RestartCount=1),
                 lambda s:s['containers'][1]['Mounts'][0].update(Destination='/different'),
                 lambda s:s['containers'][1]['State'].update(FinishedAt='2026-09-27T00:00:02Z'),
                 lambda s:s.update(boot_id='0'*36),
                 lambda s:s['volume'].update(Options={'device':'/host'}),
                 lambda s:s['container'].update(Image='sha256:'+'d'*64),
                 lambda s:s.update(listing_complete=False)]
        for change in changes:
            first=snapshot();second=deepcopy(first);change(second)
            with self.subTest(change=change),self.assertRaises(V.PreflightError):
                validate(first,second)
        value=snapshot();value['volume']['Mountpoint']='//var/lib/docker/volumes/'+V.AH.VOLUME+'/_data'
        with self.assertRaises(V.PreflightError):validate(value,deepcopy(value))
        first=snapshot();second=deepcopy(first);second['observed_at']='2026-09-27T00:00:40Z'
        with self.assertRaisesRegex(V.PreflightError,'SAMPLE_STALE'):
            V.validate_pair(first,second,now='2026-09-27T00:00:45Z')
        with self.assertRaisesRegex(V.PreflightError,'SAMPLE_STALE'):
            V.validate_pair(first,deepcopy(first),now='2026-09-27T00:01:00Z')
        early=snapshot();late=deepcopy(early)
        early['observed_at']='2026-09-27T00:00:19Z'
        late['started_at']='2026-09-27T00:00:19Z'
        late['observed_at']='2026-09-27T00:00:20Z'
        with self.assertRaisesRegex(V.PreflightError,'SAMPLE_STALE'):
            V.validate_pair(early,late,now='2026-09-27T00:00:31Z')


if __name__=='__main__':unittest.main()
