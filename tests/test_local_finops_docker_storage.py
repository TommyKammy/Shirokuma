from copy import deepcopy
import json
import unittest
from unittest.mock import Mock, patch

from tools.local_experiment.finops import docker_storage as D, contract as C, render as H, history as HISTORY, run as R
from test_local_finops_contract import NOW
from test_local_finops_process_report import report, processes
from test_local_finops_run import storage_complete, docker_storage_complete, mlflow_usage_complete


def fixture():
    context = [{'Name':D.AH.CONTEXT,'Endpoints':{'docker':{'Host':'unix:///Users/example/.colima/mac-studio-solo/docker.sock'}}}]
    source = '/var/lib/docker/volumes/' + D.AH.VOLUME + '/_data'
    container = [{'Id':'a'*64,'Name':'/'+D.AH.NAME,'Config':{'Image':D.AH.IMAGE,
                  'Labels':{D.AH.LABEL:'agenthouse-local'},'Env':['PRIVATE_SECRET=do-not-publish']},
                  'State':{'Running':False},'Mounts':[{'Type':'volume','Name':D.AH.VOLUME,
                  'Driver':'local','Source':source,'Destination':'/var/lib/clickhouse','RW':True}]}]
    volume = [{'Name':D.AH.VOLUME,'Driver':'local','Scope':'local','Labels':{D.AH.LABEL:'agenthouse-local'},
               'Mountpoint':source,'CreatedAt':'2026-09-19T00:00:00.000000000Z'}]
    return [context, container, volume]


def projected(inputs=None):
    before = fixture() if inputs is None else inputs
    return D.project(*before,*deepcopy(before),request_started_at='2026-09-20T00:00:20Z',observed_at=NOW)


class DockerStorageTests(unittest.TestCase):
    def test_fixed_stopped_container_has_reference_not_zero_or_used_bytes(self):
        row=projected()
        self.assertEqual(row['state'],'observed');self.assertIs(row['container_running'],False)
        self.assertIsNone(row['used_bytes']);self.assertEqual(row['usage_reason'],'NOT_MEASURED')
        self.assertNotIn('PRIVATE_SECRET',json.dumps(row));self.assertNotIn('/var/lib/docker/volumes/',json.dumps(row))
        data=fixture();data[2][0]['CreatedAt']='2026-09-19T09:00:00+09:00'
        self.assertEqual(projected(data)['volume_created_at'],'2026-09-19T00:00:00.000000000Z')

    def test_future_volume_creation_cannot_be_an_observed_reference(self):
        data=fixture();data[2][0]['CreatedAt']='2026-09-21T00:00:00Z'
        with self.assertRaises(D.DockerStorageError):projected(data)
        row=projected();row['volume_created_at']='2026-09-21T00:00:00Z'
        with self.assertRaises(D.DockerStorageError):D.validate_row(row)

    def test_daemon_size_hints_do_not_become_agenthouse_usage(self):
        data=fixture()
        # Neither daemon summary hints nor private mountpoints prove a safe,
        # exclusive, non-mutating per-volume traversal.
        data[2][0]['UsageData']={'Size':123456,'RefCount':1}
        data[2][0]['Size']=123456
        row=projected(data)
        self.assertIsNone(row['used_bytes'])
        self.assertEqual(row['usage_reason'],'NOT_MEASURED')
        self.assertNotIn('/var/lib/docker/volumes/',json.dumps(row))

    def test_volume_creation_offset_rejects_out_of_range_fourteen_hour_minutes(self):
        for value in ('2026-09-19T14:01:00+14:01',
                      '2026-09-19T14:59:00-14:59'):
            with self.subTest(value=value):
                data = fixture()
                data[2][0]['CreatedAt'] = value
                with self.assertRaises(D.DockerStorageError): projected(data)
        data = fixture()
        data[2][0]['CreatedAt'] = '2026-09-19T14:00:00+14:00'
        self.assertEqual(projected(data)['volume_created_at'],
                         '2026-09-19T00:00:00.000000000Z')

    def test_pre_epoch_docker_creation_is_a_scoped_invalid_sample(self):
        data = fixture()
        data[2][0]['CreatedAt'] = '1970-01-01T00:30:00+01:00'
        with self.assertRaisesRegex(D.DockerStorageError, '^SAMPLE_INVALID$'):
            projected(data)
        data[2][0]['CreatedAt'] = '2026-09-19T00:00:00Z'
        self.assertEqual(projected(data)['state'], 'observed')

    def test_foreign_context_owner_image_mount_and_source_fail_closed(self):
        changes=[lambda x:x[0][0]['Endpoints']['docker'].update(Host='tcp://elsewhere'),
                 lambda x:x[1][0]['Config']['Labels'].update({D.AH.LABEL:'foreign'}),
                 lambda x:x[2][0]['Labels'].update({D.AH.LABEL:'foreign'}),
                 lambda x:x[1][0]['Config'].update(Image='other/image'),
                 lambda x:x[1][0]['Mounts'][0].update(Name='foreign'),
                 lambda x:x[1][0]['Mounts'][0].update(Source='/host/private'),
                 lambda x:x[1][0]['Mounts'][0].update(Destination='/tmp'),
                 lambda x:x[1][0]['Mounts'].append(deepcopy(x[1][0]['Mounts'][0])),
                 lambda x:x[2][0].update(Driver='other')]
        for change in changes:
            with self.subTest(change=change):
                data=fixture();change(data)
                with self.assertRaises(D.DockerStorageError):projected(data)

    def test_volume_reference_refuses_noncanonical_mountpoint(self):
        for mountpoint in ('/var/lib/docker/volumes/foreign/../' + D.AH.VOLUME + '/_data',
                           '/var/lib/docker/volumes//' + D.AH.VOLUME + '/_data',
                           '/var/lib/docker/volumes/foreign/' + D.AH.VOLUME + '/_data'):
            with self.subTest(mountpoint=mountpoint):
                data = fixture()
                data[2][0]['Mountpoint'] = mountpoint
                data[1][0]['Mounts'][0]['Source'] = mountpoint
                with self.assertRaises(D.DockerStorageError): projected(data)

    def test_volume_reference_refuses_driver_options_without_measuring(self):
        for options in ({'type': 'none', 'device': '/host/data', 'o': 'bind'},
                        {'device': '/host/data'}, [], False):
            with self.subTest(options=options):
                data = fixture()
                data[2][0]['Options'] = options
                with self.assertRaises(D.DockerStorageError): projected(data)
        for options in (None, {}):
            data = fixture()
            data[2][0]['Options'] = options
            self.assertIsNone(projected(data)['used_bytes'])

    def test_malformed_context_and_container_labels_fail_with_scoped_error(self):
        for index, path, value in ((0, 'Endpoints', []), (0, 'docker', None),
                                   (1, 'Labels', []), (1, 'Labels', None),
                                   (2, 'Labels', [])):
            with self.subTest(index=index, path=path, value=value):
                data = fixture()
                if index == 0 and path == 'Endpoints':
                    data[0][0]['Endpoints'] = value
                elif index == 0:
                    data[0][0]['Endpoints']['docker'] = value
                elif index == 1:
                    data[1][0]['Config']['Labels'] = value
                else:
                    data[2][0]['Labels'] = value
                with self.assertRaises(D.DockerStorageError): projected(data)

    def test_context_reference_refuses_foreign_or_alias_socket(self):
        for host in ('unix:///tmp/foreign/.colima/mac-studio-solo/docker.sock',
                     'unix:///Users/example/other/.colima/mac-studio-solo/docker.sock',
                     'unix:///Users/example/../.colima/mac-studio-solo/docker.sock',
                     'unix:///Users//.colima/mac-studio-solo/docker.sock',
                     'unix:///Users/./.colima/mac-studio-solo/docker.sock',
                     'unix:///Users/example../.colima/mac-studio-solo/docker.sock',
                     'unix:///Users/example\nother/.colima/mac-studio-solo/docker.sock',
                     'unix:///Users/example%2fother/.colima/mac-studio-solo/docker.sock'):
            with self.subTest(host=host):
                data = fixture()
                data[0][0]['Endpoints']['docker']['Host'] = host
                with self.assertRaises(D.DockerStorageError): projected(data)

    def test_before_after_identity_and_mount_drift_refused(self):
        for index,change in ((1,lambda x:x[0].update(Id='b'*64)),
                             (1,lambda x:x[0]['State'].update(Running=True)),
                             (2,lambda x:x[0].update(CreatedAt='2026-09-18T00:00:00Z')),
                             (2,lambda x:x[0].update(Mountpoint='/var/lib/docker/volumes/foreign/_data'))):
            first=fixture();later=deepcopy(first);change(later[index])
            with self.subTest(index=index,change=change),self.assertRaises(D.DockerStorageError):
                D.project(*first,*later,request_started_at='2026-09-20T00:00:20Z',observed_at=NOW)

    def test_bounded_reads_no_lifecycle_and_cleanup_propagates(self):
        backend=Mock();backend.now.return_value=NOW
        backend.docker_storage_inspect.side_effect=fixture()+deepcopy(fixture())
        result=D.collect(backend);self.assertEqual(result['agenthouse']['state'],'observed')
        self.assertEqual([c.args for c in backend.docker_storage_inspect.call_args_list],
            [('context',D.AH.CONTEXT),('container',D.AH.NAME),('volume',D.AH.VOLUME)]*2)
        backend.docker_storage_inspect.side_effect=RuntimeError('PRIVATE')
        self.assertEqual(D.collect(backend)['agenthouse']['reason'],'READ_FAILED')
        backend.docker_storage_inspect.side_effect=R.U.PodUsageCleanupError()
        with self.assertRaises(R.U.PodUsageCleanupError):D.collect(backend)
        outer=Mock();outer.now.return_value=NOW;outer.component.side_effect=RuntimeError('stopped')
        outer.disk.side_effect=RuntimeError('stopped');outer.inference.side_effect=RuntimeError('stopped')
        outer.local_processes.side_effect=R.U.PodUsageCleanupError()
        R.collect(outer);outer.docker_storage.assert_not_called()

    def test_backend_only_fixed_scoped_inspect_commands(self):
        backend=R.Backend()
        with patch.object(backend,'_command',side_effect=lambda cmd:json.dumps([{'safe':'metadata'}])) as command:
            for kind,name in (('context',D.AH.CONTEXT),('container',D.AH.NAME),('volume',D.AH.VOLUME)):
                self.assertEqual(backend.docker_storage_inspect(kind,name),[{'safe':'metadata'}])
            self.assertEqual([x.args[0] for x in command.call_args_list],[
                ['docker','context','inspect',D.AH.CONTEXT],
                ['docker','--context',D.AH.CONTEXT,'container','inspect',D.AH.NAME],
                ['docker','--context',D.AH.CONTEXT,'volume','inspect',D.AH.VOLUME]])
            with self.assertRaises(C.FinOpsError):backend.docker_storage_inspect('volume','foreign')
            self.assertEqual(command.call_count,3)

    def test_v4_public_roundtrip_render_history_and_legacy_unavailable(self):
        old=report(processes())
        current=C.build_report(old['components'],old['disks'],old['inference'],observed_at=NOW,
                               local_processes=processes(),storage=storage_complete(),
                               docker_storage=docker_storage_complete())
        self.assertEqual(current['schema_version'],4)
        self.assertEqual(C.validate_public(current),current)
        html=H.render(current);self.assertIn('AgentHouseのDocker volume参照',html)
        self.assertIn('未計測（NOT_MEASURED）',html)
        historical=HISTORY.render(HISTORY.parse(json.dumps([report(),old,current]).encode()))
        self.assertIn('id="observation-3-docker-storage"',historical)
        self.assertEqual(historical.count('このschemaにはDocker volume参照は含まれません'),2)
        current['docker_storage']['agenthouse']['used_bytes']='10'
        with self.assertRaises((C.FinOpsError,D.DockerStorageError)):C.validate_public(current)

    def test_saved_v7_cannot_gain_volume_bytes_retroactively(self):
        old=report(processes())
        current=C.build_report(old['components'],old['disks'],old['inference'],observed_at=NOW,
            local_processes=processes(),storage=storage_complete(),docker_storage=docker_storage_complete(),
            host_storage=mlflow_usage_complete())
        self.assertEqual(current['schema_version'],7)
        self.assertIsNone(current['docker_storage']['agenthouse']['used_bytes'])
        for field,value in (('used_bytes','123'),('usage_reason',None)):
            changed=deepcopy(current);changed['docker_storage']['agenthouse'][field]=value
            with self.subTest(field=field),self.assertRaises((C.FinOpsError,D.DockerStorageError)):
                C.validate_public(changed)


if __name__=='__main__': unittest.main()
