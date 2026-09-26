from copy import deepcopy
import unittest
import uuid

from tools.local_experiment.finops import linux_volume_receipt_gate as G


OPERATION=str(uuid.uuid4())
BOOT='01234567-89ab-cdef-0123-456789abcdef'
DIGEST='a'*64
HOST_BOOT='fedcba98-7654-3210-fedc-ba9876543210'


def sample():
    record={'operation_id':OPERATION,'worker_digest':DIGEST,'boot_id':BOOT,
            'state':'complete','reason':None,'child_pid':1412,'child_start':'123456',
            'cleanup_verified':True,'result':{'logical_regular_file_bytes':'42',
                                             'regular_file_count':1,'directory_count':0}}
    probe={'boot_id':BOOT,'pid':1412,'start_ticks':None,
           'host_boot_id':HOST_BOOT,
           'observed_monotonic':110.0,'query_complete':True}
    return record,probe


def accept(record,probe):
    return G.accept(record,probe,operation_id=OPERATION,worker_digest=DIGEST,
                    boot_id=BOOT,host_boot_id=HOST_BOOT,
                    operation_started_monotonic=100.0,now_monotonic=115.0)


class ReceiptGateTests(unittest.TestCase):
    def test_exact_reaped_child_receipt_accepts_private_logical_counts(self):
        record,probe=sample()
        self.assertEqual(accept(record,probe),record['result'])
        probe['start_ticks']='999999'  # PID reused after the worker was reaped.
        self.assertEqual(accept(record,probe),record['result'])
        probe['start_ticks']='0123456'
        with self.assertRaises(G.ReceiptError):accept(record,probe)

    def test_active_child_missing_probe_or_stale_timestamp_rejected(self):
        for change in (lambda r,p:p.update(start_ticks=r['child_start']),
                       lambda r,p:p.update(query_complete=False),
                       lambda r,p:p.update(observed_monotonic=50.0),
                       lambda r,p:p.update(observed_monotonic=116.0),
                       lambda r,p:p.update(start_ticks='unknown'),
                       lambda r,p:p.update(host_boot_id='f'*36),
                       lambda r,p:p.update(pid=1413),
                       lambda r,p:p.update(boot_id='f'*36)):
            record,probe=sample();change(record,probe)
            with self.subTest(change=change),self.assertRaises(G.ReceiptError):accept(record,probe)

    def test_unverified_or_forged_receipts_rejected(self):
        for change in (lambda r,p:r.update(operation_id=str(uuid.uuid4())),
                       lambda r,p:r.update(worker_digest='b'*64),
                       lambda r,p:r.update(boot_id='f'*36),
                       lambda r,p:r.update(cleanup_verified=False),
                       lambda r,p:r.update(state='running'),
                       lambda r,p:r.update(reason='READ_FAILED'),
                       lambda r,p:r['result'].update(logical_regular_file_bytes='042'),
                       lambda r,p:r['result'].update(logical_regular_file_bytes='9'*10000),
                       lambda r,p:r.update(child_start='0123456'),
                       lambda r,p:r['result'].update(regular_file_count=True),
                       lambda r,p:r.update(result=None)):
            record,probe=sample();change(record,probe)
            with self.subTest(change=change),self.assertRaises(G.ReceiptError):accept(record,probe)
        record,probe=sample();record['state']='unknown';record['reason']='READ_TIMEOUT';record['result']=None
        with self.assertRaisesRegex(G.ReceiptError,'READ_FAILED'):accept(record,probe)
        record,probe=sample()
        with self.assertRaisesRegex(G.ReceiptError,'CLEANUP_UNVERIFIED'):
            G.accept(record,probe,operation_id=OPERATION,worker_digest=DIGEST,
                     boot_id=BOOT,host_boot_id=HOST_BOOT,
                     operation_started_monotonic=80.0,now_monotonic=115.0)


if __name__=='__main__':unittest.main()
