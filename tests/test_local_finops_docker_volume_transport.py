from copy import deepcopy
import json
from pathlib import Path
import unittest
from unittest.mock import Mock

from tools.local_experiment.finops import docker_volume_transport as T, docker_volume_preflight as V
from test_local_finops_docker_volume_preflight import snapshot


NOW='2026-09-27T00:00:00Z'


def backend(*, listing=None, missing_volume=False, foreign_context=False, foreign_options=False):
    value=snapshot();context=deepcopy(value['context']);volume=deepcopy(value['volume'])
    endpoint='unix://'+str(Path.home()/'.colima/mac-studio-solo/docker.sock')
    context['Endpoints']['docker']['Host']='unix:///Users/foreign/.colima/mac-studio-solo/docker.sock' if foreign_context else endpoint
    docker=('docker','--host',endpoint)
    volume['Labels']['SECRET']='do-not-publish'
    if foreign_options:volume['Options']={'o':'username=secret,password=do-not-publish'}
    containers=deepcopy(value['containers'])
    containers[1]['Config']={'Labels':None,'Image':'other'}
    containers[1]['HostConfig']={'RestartPolicy':{'Name':'no'}}
    containers[0]['Config']['Env']=['PRIVATE_TOKEN=do-not-publish']
    containers[0]['State']['Error']='do-not-publish'
    containers[0]['Mounts'][0]['Mode']='do-not-publish'
    info='|'.join(json.dumps(value['daemon'][key]) for key in
                  ('ID','OSType','Architecture','DockerRootDir'))
    responses={('docker','context','inspect',V.AH.CONTEXT):json.dumps([context]),
       (*docker,'info','--format',
        '{{json .ID}}|{{json .OSType}}|{{json .Architecture}}|{{json .DockerRootDir}}'):info,
       (*docker,'volume','inspect',V.AH.VOLUME):
        json.dumps([] if missing_volume else [volume]),
       (*docker,'container','ls','-a','--no-trunc','--format','{{.ID}}'):
        '\n'.join(value['listed_ids'] if listing is None else listing)+'\n',
       ('colima','ssh','--profile','mac-studio-solo','--','cat','/proc/sys/kernel/random/boot_id'):
        value['boot_id']+'\n'}
    for item in containers:
        responses[(*docker,'container','inspect',item['Id'])]=json.dumps([item])
    calls=[]
    def command(args, *, timeout):
        key=tuple(args);calls.append(key)
        if timeout <= 0:raise AssertionError('unbounded transport')
        if key not in responses:raise AssertionError('unexpected read command')
        return responses[key]
    tool=Mock();tool._command.side_effect=command;tool.now.return_value=NOW
    return tool,calls


class VolumeTransportTests(unittest.TestCase):
    def test_bounded_fixed_reads_project_secrets_before_offline_guard(self):
        tool,calls=backend();data=T.collect(tool)
        self.assertEqual(len(calls),9)
        self.assertEqual(data['listed_ids'],snapshot()['listed_ids'])
        self.assertNotIn('do-not-publish',repr(data))
        self.assertEqual(V.validate_pair(data,deepcopy(data),now='2026-09-27T00:00:10Z')
                         ['volume_traversal_authorized'],False)
        self.assertTrue(all('create' not in call and 'start' not in call and
                            'mount' not in call and 'exec' not in call for call in calls))

    def test_missing_volume_or_oversized_list_never_triggers_create_or_inspect(self):
        tool,calls=backend(missing_volume=True)
        with self.assertRaises(T.TransportError):T.collect(tool)
        self.assertEqual(len(calls),3)
        self.assertFalse(any('container' in call for call in calls))
        tool,calls=backend(listing=['b'*64]*(V.MAX_CONTAINERS+1))
        with self.assertRaises(T.TransportError):T.collect(tool)
        self.assertEqual(len(calls),4)
        self.assertFalse(any('inspect' in call and 'container' in call for call in calls))
        tool,calls=backend(foreign_options=True)
        with self.assertRaises(T.TransportError):T.collect(tool)
        self.assertEqual(len(calls),3)
        self.assertNotIn('do-not-publish',repr(calls))

    def test_foreign_context_rejected_before_any_daemon_read(self):
        tool,calls=backend(foreign_context=True)
        with self.assertRaises(T.TransportError):T.collect(tool)
        self.assertEqual(len(calls),1)

    def test_container_listing_requires_docker_lf_framing(self):
        for separator in ('\u2028', '\r\n', '\x85'):
            with self.subTest(separator=repr(separator)):
                tool,calls=backend()
                original=tool._command.side_effect
                def changed(args, *, timeout):
                    raw=original(args,timeout=timeout)
                    if 'ls' in args:
                        return raw.replace('\n', separator)
                    return raw
                tool._command.side_effect=changed
                with self.assertRaises(T.TransportError):T.collect(tool)
                self.assertFalse(any('inspect' in call and 'container' in call for call in calls))

    def test_new_container_after_inspection_rejected_by_second_list(self):
        tool,calls=backend();original=tool._command.side_effect;lists=[0]
        def changed(args, *, timeout):
            raw=original(args,timeout=timeout)
            if 'ls' in args:
                lists[0]+=1
                if lists[0]==2:return raw+'d'*64+'\n'
            return raw
        tool._command.side_effect=changed
        with self.assertRaises(T.TransportError):T.collect(tool)
        self.assertEqual(lists[0],2)
        self.assertFalse(any('colima' in call for call in calls))

    def test_daemon_replacement_during_collection_rejected(self):
        tool,calls=backend();original=tool._command.side_effect;infos=[0]
        def changed(args, *, timeout):
            raw=original(args,timeout=timeout)
            if 'info' in args:
                infos[0]+=1
                if infos[0]==2:return raw.replace('DAEMON:ONE','DAEMON:TWO')
            return raw
        tool._command.side_effect=changed
        with self.assertRaises(T.TransportError):T.collect(tool)
        self.assertEqual(infos[0],2)

    def test_duplicate_json_and_incomplete_inspect_rejected(self):
        tool,calls=backend()
        original=tool._command.side_effect
        def broken(args, *, timeout):
            if args[:3]==['docker','context','inspect']:
                return '[{"Name":"first","Name":"second"}]'
            return original(args,timeout=timeout)
        tool._command.side_effect=broken
        with self.assertRaises(T.TransportError):T.collect(tool)
        self.assertEqual(len(calls),0)

    def test_collection_deadline_disallows_more_commands(self):
        tool,calls=backend();clock=[0.0]
        original=tool._command.side_effect
        def slow(args, *, timeout):
            raw=original(args,timeout=timeout);clock[0]+=21.0;return raw
        tool._command.side_effect=slow
        with self.assertRaises(T.TransportError):T.collect(tool,monotonic=lambda:clock[0])
        self.assertEqual(len(calls),1)


if __name__=='__main__':unittest.main()
