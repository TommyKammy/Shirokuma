"""Owned Grafana migration with fake Docker and a private synthetic SQLite DB."""
from copy import deepcopy
import io
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import tarfile
import tempfile
import unittest
from unittest.mock import patch

from tools.local_experiment.activation import runtime as A
from tests.test_local_grafana_runtime import container, credential, M

G = A.G
OLD, NEW = 'a' * 64, 'b' * 64


class MigrationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(); self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.state = self.root / 'grafana'; self.state.mkdir(mode=0o700)
        for target,name,value in ((G,'STATE',self.state),(M,'STATE',self.state),(A,'ROOT',self.root)):
            item = patch.object(target,name,value);item.start();self.addCleanup(item.stop)
        self.credential = credential()
        G.private_write(self.state/'credentials.json', self.credential)
        G.private_write(self.state/'container.env', A.env_text(self.credential,False).decode())
        G.private_write(self.state/'reader-users.xml', G.reader_xml(self.credential,G.HISTORY_READER_TABLES))
        original = container();original.update(Id=OLD,Name='/'+G.NAME)
        self.containers = {OLD:original}
        self.calls=[];self.copies=0;self.fail_rename=False;self.extra_writer=False;self.copy_error=None;self.wal=False
        self.database = self.root/'source.db'
        with sqlite3.connect(self.database) as db:
            db.execute('CREATE TABLE alert_rule (id INTEGER PRIMARY KEY, title TEXT)')
        network = dict(Id='network-id',Driver='bridge',Internal=True,Labels={G.LABEL:G.OWNER})
        frontend = dict(Id='frontend-id',Driver='bridge',Internal=False,Labels={G.LABEL:G.OWNER})
        self.networks={G.NETWORK:network,G.FRONTEND_NETWORK:frontend}
        patches=[patch.object(G,'check_context'),patch.object(G,'inspect',side_effect=self.inspect),
            patch.object(G,'docker',side_effect=self.docker),patch.object(G,'prepare_plugin',return_value={}),
            patch.object(G,'agenthouse',return_value={'Id':'agenthouse'}),
            patch.object(G,'ensure_network',return_value=network),patch.object(G,'ensure_frontend',return_value=frontend),
            patch.object(G,'ensure_reader',return_value={'select_only':True}),patch.object(G,'attach_frontend'),
            patch.object(G,'api',side_effect=self.api),patch.object(G,'request',return_value=json.dumps(
                {'dashboard':{'uid':G.DASHBOARD_UID},'meta':{'provisioned':True}})),
            patch.object(A.subprocess,'run',side_effect=self.copy)]
        for item in patches:item.start();self.addCleanup(item.stop)

    def inspect(self,kind,name):
        if kind=='container':
            value=self.containers.get(name) or next((v for v in self.containers.values() if v['Name']=='/'+name),None)
            return deepcopy(value)
        if kind=='volume':return dict(Name=G.VOLUME,Driver='local',Options=None,Labels={G.LABEL:G.OWNER},CreatedAt='fixed')
        if kind=='network':return deepcopy(self.networks[name])
        raise AssertionError('unexpected inspect')

    def docker(self,*args,**kwargs):
        self.calls.append(args)
        if args[:2]==('image','inspect'):
            return json.dumps([dict(Architecture='arm64',Os='linux',Config={'Env':[]})])
        if args[0]=='ps':
            return ('other-writer\n' if self.extra_writer else '')+''.join(v['Id']+'\n' for v in self.containers.values() if v['State']['Running'])
        if args[0]=='stop':self.containers[args[-1]]['State']['Running']=False;return ''
        if args[0]=='rename':
            self.assertFalse(any(v['Name']=='/'+args[2] for v in self.containers.values()))
            self.containers[args[1]]['Name']='/'+args[2]
            if self.fail_rename:
                self.fail_rename=False
                raise OSError('response lost after rename')
            return ''
        if args[0]=='create':
            mode=G.alerting_mode(self.credential)
            receipt=json.loads((A.directory(mode['operation_token'])/'backup.json').read_bytes())
            self.assertEqual(receipt['alert_rule_count'],0)
            self.assertIn('shirokuma.dev.activation='+mode['operation_token'],args)
            self.assertFalse(self.containers[OLD]['State']['Running'])
            value=deepcopy(self.containers[OLD]);value.update(Id=NEW,Name='/'+G.NAME)
            value['Config']['Env']=(self.state/'container.env').read_text().splitlines()
            value['Config']['Labels'][A.LABEL]=mode['operation_token']
            value['NetworkSettings']['Networks'].pop(G.NETWORK)
            self.containers[NEW]=value
            return NEW
        if args[:2]==('network','connect'):
            self.containers[args[-1]]['NetworkSettings']['Networks'][G.NETWORK]={'NetworkID':args[2]}
            return ''
        if args[0]=='start':self.containers[args[-1]]['State']['Running']=True;return ''
        raise AssertionError('unexpected Docker mutation: '+args[0])

    def api(self,path,credential=None):
        if path=='/api/health':return dict(database='ok',version=G.PINS['grafana']['version'])
        if path.endswith('/settings'):return dict(signature='valid',info={'version':G.PINS['plugin']['version']})
        if path.endswith('/health'):return {'status':'OK'}
        return dict(type=G.PINS['plugin']['id'],readOnly=True,secureJsonFields={'password':True},
            jsonData=dict(username=self.credential['reader_user'],host=G.AH.NAME,protocol='http',port=8123,queryTimeout=G.PLUGIN_QUERY_TIMEOUT))

    def copy(self,args,**kwargs):
        self.copies+=1
        self.assertEqual(args,['rtk','proxy','docker','--context',G.CONTEXT,'cp',OLD+':/var/lib/grafana/.','-'])
        self.assertFalse(self.containers[OLD]['State']['Running'])
        if self.copy_error:
            kwargs['stdout'].write(b'partial archive')
            raise self.copy_error
        with tarfile.open(fileobj=kwargs['stdout'],mode='w') as archive:
            raw=self.database.read_bytes();info=tarfile.TarInfo('grafana.db');info.size=len(raw)
            archive.addfile(info,io.BytesIO(raw))
            if self.wal:
                info=tarfile.TarInfo('grafana.db-wal');info.size=3
                archive.addfile(info,io.BytesIO(b'wal'))
        return subprocess.CompletedProcess(args,0)

    def operation(self):
        paths=list((self.state/'alerting-operations').glob('*/receipt.json'))
        self.assertEqual(len(paths),1)
        return json.loads(paths[0].read_bytes())

    def test_enable_retains_original_and_cold_copy_then_selected_restart_and_rollback(self):
        credentials=(self.state/'credentials.json').read_bytes();reader=(self.state/'reader-users.xml').read_bytes()
        result=A.enable();token=result['operation_token'];record=self.operation()
        self.assertEqual(result['result'],'passed');self.assertTrue(result['backup_verified'])
        self.assertEqual(self.copies,1);self.assertEqual(record['backup']['alert_rule_count'],0)
        self.assertEqual(self.containers[OLD]['Name'],'/'+record['retained_name'])
        self.assertFalse(self.containers[OLD]['State']['Running']);self.assertTrue(self.containers[NEW]['State']['Running'])
        self.assertEqual(G.environment(self.credential)['GF_UNIFIED_ALERTING_ENABLED'],'false')
        self.assertEqual(G.selected_alerting(self.credential),True)
        G.stop();self.assertFalse(self.containers[NEW]['State']['Running'])
        self.assertTrue(G.up()['ready']);self.assertEqual(self.containers[NEW]['Id'],NEW)
        backup=(A.directory(token)/'volume.tar').read_bytes()
        self.assertEqual(A.resume(token)['result'],'passed');self.assertEqual(self.copies,1)
        rolled=A.rollback(token)
        self.assertEqual(rolled['result'],'rolled-back');self.assertFalse(G.selected_alerting(self.credential))
        self.assertEqual(self.containers[OLD]['Name'],'/'+G.NAME);self.assertTrue(self.containers[OLD]['State']['Running'])
        self.assertEqual(self.containers[NEW]['Name'],'/'+record['retained_new_name'])
        self.assertFalse(self.containers[NEW]['State']['Running'])
        self.assertEqual((A.directory(token)/'volume.tar').read_bytes(),backup)
        self.assertEqual((self.state/'credentials.json').read_bytes(),credentials)
        self.assertEqual((self.state/'reader-users.xml').read_bytes(),reader)
        self.assertFalse(any(c[0] in ('rm','volume') for c in self.calls))
        self.assertEqual(A.rollback(token)['result'],'rolled-back')

    def test_unknown_rename_is_reconciled_without_second_copy_or_rename(self):
        self.fail_rename=True
        with self.assertRaises(OSError):A.enable()
        record=self.operation();self.assertEqual(record['pending'],'retain-original')
        with self.assertRaises(G.GrafanaError):G.up()
        self.assertEqual(A.resume(record['token'])['result'],'passed')
        self.assertEqual(self.copies,1)
        self.assertEqual(sum(c[0]=='rename' for c in self.calls),1)

    def test_existing_rules_refuse_before_engine_creation_and_allow_config_rollback(self):
        with sqlite3.connect(self.database) as db:db.execute("INSERT INTO alert_rule VALUES (1,'existing')")
        with self.assertRaises(A.ActivationError):A.enable()
        record=self.operation();self.assertIsNone(record['backup']);self.assertEqual(record['pending'],'cold-backup')
        self.assertTrue((A.directory(record['token'])/'volume.tar').exists())
        self.assertFalse(any(c[0]=='create' for c in self.calls))
        self.assertEqual(A.rollback(record['token'])['result'],'rolled-back')

    def test_unknown_database_schema_retains_copy_and_refuses_engine(self):
        with sqlite3.connect(self.database) as db:db.execute('DROP TABLE alert_rule')
        with self.assertRaises(A.ActivationError):A.enable()
        record=self.operation();base=A.directory(record['token'])
        self.assertTrue((base/'volume.tar').exists());self.assertTrue((base/'before-grafana.db').exists())
        self.assertFalse((base/'backup.json').exists())
        self.assertFalse(any(c[0]=='create' for c in self.calls))

    def test_uncheckpointed_database_sidecar_refuses_immutable_zero_count(self):
        self.wal=True
        with self.assertRaises(A.ActivationError):A.enable()
        record=self.operation();self.assertIsNone(record['backup'])
        self.assertFalse((A.directory(record['token'])/'before-grafana.db').exists())
        self.assertFalse(any(c[0]=='create' for c in self.calls))

    def test_unknown_backup_is_retained_and_not_automatically_retried(self):
        self.copy_error=OSError('unknown copy')
        with self.assertRaises(OSError):A.enable()
        record=self.operation();path=A.directory(record['token'])/'volume.tar';before=path.read_bytes()
        self.copy_error=None
        with self.assertRaises(A.ActivationError):A.resume(record['token'])
        self.assertEqual(self.copies,1);self.assertEqual(path.read_bytes(),before)
        self.assertFalse(any(c[0]=='create' for c in self.calls))

    def test_unrelated_volume_writer_prevents_copy_and_new_engine(self):
        self.extra_writer=True
        with self.assertRaises(A.ActivationError):A.enable()
        self.assertEqual(self.copies,0);self.assertFalse(any(c[0]=='create' for c in self.calls))

    def test_unknown_environment_refuses_before_stopping(self):
        self.containers[OLD]['Config']['Env'].append('GF_SMTP_ENABLED=true')
        with self.assertRaises(A.ActivationError):A.enable()
        self.assertTrue(self.containers[OLD]['State']['Running'])
        self.assertFalse(any(c[0]=='stop' for c in self.calls))

    def test_mount_permutations_have_one_configuration_identity(self):
        original=self.containers[OLD];reordered=deepcopy(original)
        reordered['Mounts'].reverse()
        self.assertEqual(A.configuration(original),A.configuration(reordered))
        reordered['Mounts'][0]['RW']=not reordered['Mounts'][0]['RW']
        self.assertNotEqual(A.configuration(original),A.configuration(reordered))

    def test_pre_effect_legacy_mount_hash_rebind_preserves_exact_failure_receipt(self):
        with patch.object(A,'forward',side_effect=OSError('before stop')),self.assertRaises(OSError):A.enable()
        record=self.operation();token=record['token'];original=self.containers[OLD]
        record['configuration_sha256']=A.digest({k:original[k] for k in ('Config','HostConfig','Mounts')})
        A.save(record);prior=(A.directory(token)/'receipt.json').read_bytes()
        original['Mounts'].reverse()
        self.assertEqual(A.resume(token)['result'],'passed')
        retained=A.directory(token)/'receipt-before-mount-order.json'
        self.assertEqual(retained.read_bytes(),prior);self.assertEqual(retained.stat().st_mode&0o777,0o600)
        self.assertEqual(self.copies,1)

    def test_legacy_rebind_does_not_accept_changed_configuration(self):
        with patch.object(A,'forward',side_effect=OSError('before stop')),self.assertRaises(OSError):A.enable()
        record=self.operation();token=record['token'];original=self.containers[OLD]
        record['configuration_sha256']=A.digest({k:original[k] for k in ('Config','HostConfig','Mounts')})
        A.save(record);prior=(A.directory(token)/'receipt.json').read_bytes()
        original['Config']['Env'].append('UNEXPECTED_CHANGED_VALUE=1')
        with self.assertRaises(A.ActivationError):A.resume(token)
        self.assertEqual((A.directory(token)/'receipt.json').read_bytes(),prior)
        self.assertFalse((A.directory(token)/'receipt-before-mount-order.json').exists())
        self.assertFalse(any(c[0]=='stop' for c in self.calls))

    def test_changed_credentials_reader_or_selected_identity_refuse(self):
        token=A.enable()['operation_token']
        value=G.private_read(self.state/'alerting-mode.json');value['container_id']='f'*64
        G.private_write(self.state/'alerting-mode.json',value)
        with self.assertRaises(G.GrafanaError):G.status()
        value['container_id']=NEW;G.private_write(self.state/'alerting-mode.json',value)
        (self.state/'reader-users.xml').write_bytes(b'changed')
        with self.assertRaises(A.ActivationError):A.rollback(token)
        self.assertTrue(self.containers[NEW]['State']['Running'])

    def test_backup_corruption_prevents_resume(self):
        self.fail_rename=True
        with self.assertRaises(OSError):A.enable()
        token=self.operation()['token'];(A.directory(token)/'volume.tar').write_bytes(b'changed')
        with self.assertRaises(A.ActivationError):A.resume(token)
        self.assertFalse(any(c[0]=='create' for c in self.calls))

    def test_first_cancellation_survives_failure_receipt_write(self):
        first=KeyboardInterrupt('original');self.copy_error=first
        save=A.save
        def fail(record):
            if record['result']=='failed':raise OSError('secondary')
            return save(record)
        with patch.object(A,'save',side_effect=fail),self.assertRaises(KeyboardInterrupt) as raised:A.enable()
        self.assertIs(raised.exception,first)
        self.assertFalse(any(c[0]=='create' for c in self.calls))

    def test_private_read_preserves_original_cancellation_if_close_fails(self):
        original=KeyboardInterrupt('original');fdopen=A.os.fdopen
        class Broken:
            def __init__(self,fd,mode):self.inner=fdopen(fd,mode)
            def fileno(self):return self.inner.fileno()
            def read(self,*args):raise original
            def close(self):self.inner.close();raise OSError('secondary close')
        with patch.object(A.os,'fdopen',side_effect=Broken),self.assertRaises(KeyboardInterrupt) as raised:
            A.read_bytes(self.state/'container.env')
        self.assertIs(raised.exception,original)

    def test_marker_privacy_owner_and_pending_mode_are_strict(self):
        with patch.object(A,'forward',side_effect=OSError('before effects')),self.assertRaises(OSError):A.enable()
        with self.assertRaises(G.GrafanaError):G.prepare()
        path=self.state/'alerting-mode.json';value=G.private_read(path)
        value['owner_token']='f'*32;G.private_write(path,value)
        with self.assertRaises(G.GrafanaError):G.selected_alerting(self.credential)
        path.chmod(0o644)
        with self.assertRaises(G.GrafanaError):G.selected_alerting(self.credential)


if __name__=='__main__':unittest.main()
