from copy import deepcopy
import unittest

from tools.local_experiment.finops import docker_volume_preflight as V
from test_local_finops_docker_storage import fixture


def snapshot():
    context, containers, volumes = fixture()
    container=containers[0];container['Image']='sha256:'+'b'*64
    container['State'].update(Status='exited',Restarting=False,
        StartedAt='2026-09-20T00:00:00Z',FinishedAt='2026-09-20T00:00:10Z')
    container['RestartCount']=0
    container['HostConfig']={'RestartPolicy':{'Name':'no'}}
    volume=volumes[0];volume['Options']=None
    other={'Id':'c'*64,'State':{'Status':'exited','Running':False,'Restarting':False,
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
    def test_stopped_fixed_volume_is_metadata_only_not_scan_permission(self):
        value=snapshot();result=validate(value,deepcopy(value))
        self.assertEqual(result,{'state':'metadata_eligible','container_count':2,
                                 'vm_host_writers_unresolved':True,'volume_traversal_authorized':False})
        self.assertNotIn('/var/lib/docker',repr(result))
        value['volume']['Options']={}
        self.assertEqual(validate(value,deepcopy(value)),result)

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
