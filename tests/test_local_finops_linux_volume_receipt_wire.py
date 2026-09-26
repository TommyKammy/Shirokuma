from copy import deepcopy
import json
import unittest
import uuid

from tools.local_experiment.finops import linux_volume_receipt_wire as W


NONCE=str(uuid.uuid4())
OPERATION=str(uuid.uuid4())
BOOT=str(uuid.uuid4())
DIGEST='a'*64
VM={'boot_id':BOOT,'pid_namespace':4026531836,'mount_namespace':4026531841,
    'proc_device':32,'proc_inode':1,'proc_mount_id':19}


def envelope():
    record={'operation_id':OPERATION,'worker_digest':DIGEST,'boot_id':BOOT,
            'state':'complete','reason':None,'child_pid':345,'child_start':'123456',
            'cleanup_verified':True,'result':{'logical_regular_file_bytes':'42',
                                             'regular_file_count':1,'directory_count':0}}
    return {'version':1,'nonce':NONCE,'bundle_digest':DIGEST,'vm_before':deepcopy(VM),
            'vm_after':deepcopy(VM),'record':record,
            'probe':{'pid':345,'start_ticks':None,'pid_namespace':VM['pid_namespace'],
                     'proc_mount_id':VM['proc_mount_id'],'query_complete':True}}


def parse(value):
    return W.parse(json.dumps(value,separators=(',',':')).encode(),nonce=NONCE,
                   operation_id=OPERATION,expected_bundle_digest=DIGEST,vm_pin=VM)


class ReceiptWireTests(unittest.TestCase):
    def test_terminal_fixture_wire_is_only_a_candidate_not_authentication(self):
        value=envelope()
        self.assertEqual(parse(value),{'record':value['record'],'probe':value['probe']})
        value['probe']['start_ticks']='99999'
        self.assertEqual(parse(value)['record']['result']['logical_regular_file_bytes'],'42')

    def test_replay_wrong_bundle_namespace_boot_and_live_child_rejected(self):
        changes=[lambda x:x.update(version=2),
                 lambda x:x.update(nonce=str(uuid.uuid4())),
                 lambda x:x.update(bundle_digest='b'*64),
                 lambda x:x['vm_before'].update(boot_id=str(uuid.uuid4())),
                 lambda x:x['vm_after'].update(pid_namespace=55),
                 lambda x:x['vm_before'].update(proc_mount_id=20),
                 lambda x:x['record'].update(operation_id=str(uuid.uuid4())),
                 lambda x:x['record'].update(worker_digest='b'*64),
                 lambda x:x['record'].update(cleanup_verified=False),
                 lambda x:x['probe'].update(query_complete=False),
                 lambda x:x['probe'].update(pid_namespace=55),
                 lambda x:x['probe'].update(pid_namespace=float(VM['pid_namespace'])),
                 lambda x:x['probe'].update(proc_mount_id=20),
                 lambda x:x['probe'].update(start_ticks='123456'),
                 lambda x:x['probe'].update(start_ticks='0123456'),
                 lambda x:x['record']['result'].update(logical_regular_file_bytes='9'*10000)]
        for change in changes:
            value=envelope();change(value)
            with self.subTest(change=change),self.assertRaises(W.WireError):parse(value)

    def test_unknown_and_malformed_json_never_publish_partial_result(self):
        value=envelope();value['record'].update(state='unknown',reason='READ_TIMEOUT',result=None)
        self.assertIsNone(parse(value)['record']['result'])
        value['record']['reason']={'fake':True}
        with self.assertRaises(W.WireError):parse(value)
        for raw in (b'',b'{}',b'{"a":1,"a":2}',b'\xff',b'{"a":NaN}',
                    b'{"a":1e999}',
                    json.dumps(envelope()).encode()+b' trailing',b'x'*(W.MAX_WIRE+1)):
            with self.subTest(raw=raw[:25]),self.assertRaises(W.WireError):
                W.parse(raw,nonce=NONCE,operation_id=OPERATION,
                        expected_bundle_digest=DIGEST,vm_pin=VM)


if __name__=='__main__':unittest.main()
