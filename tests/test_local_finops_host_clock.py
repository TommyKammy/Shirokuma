from copy import deepcopy
import platform
import unittest
import uuid
from unittest.mock import patch

from tools.local_experiment.finops import host_clock as H
from tools.local_experiment.finops import linux_volume_receipt_gate as G
from test_local_finops_linux_volume_receipt_gate import sample as receipt, OPERATION, BOOT, DIGEST

HOST_BOOT='a1111111-2222-4333-8444-555555555555'


def samples():
    return tuple({'boot_id':HOST_BOOT,'awake_ns':v*1_000_000_000,
                  'continuous_ns':v*1_000_000_000} for v in (100,110,115))


def accept(start,probe_time,now):
    record,probe=receipt();probe['host_boot_id']=HOST_BOOT
    return G.accept_with_clock(record,probe,operation_id=OPERATION,
                               worker_digest=DIGEST,boot_id=BOOT,
                               start_sample=start,probe_sample=probe_time,
                               now_sample=now)


class HostClockTests(unittest.TestCase):
    def test_three_fresh_same_boot_samples_allow_only_structural_fixture(self):
        start,probe,now=samples()
        self.assertEqual(H.validate(start,probe,now)['host_boot_id'],HOST_BOOT)
        self.assertEqual(accept(start,probe,now)['logical_regular_file_bytes'],'42')

    def test_sleep_boot_reset_and_stale_probe_reject_before_receipt(self):
        for index,field,value in ((1,'continuous_ns',112_000_000_000),
                                  (1,'continuous_ns',109_000_000_000),
                                  (2,'boot_id',str(uuid.uuid4())),
                                  (1,'awake_ns',99_000_000_000),
                                  (2,'awake_ns',150_000_000_000),
                                  (2,'continuous_ns',150_000_000_000),
                                  (1,'awake_ns',True),
                                  (2,'continuous_ns',2**65)):
            parts=list(samples());parts[index]=deepcopy(parts[index]);parts[index][field]=value
            with self.subTest(index=index,field=field,value=value),self.assertRaises(G.ReceiptError):
                accept(*parts)

    def test_probe_timestamp_and_boot_must_match_trusted_samples(self):
        start,probe_time,now=samples();record,probe=receipt()
        probe['host_boot_id']=HOST_BOOT;probe['observed_monotonic']=109.0
        with self.assertRaises(G.ReceiptError):
            G.accept_with_clock(record,probe,operation_id=OPERATION,
                                worker_digest=DIGEST,boot_id=BOOT,
                                start_sample=start,probe_sample=probe_time,now_sample=now)
        probe['observed_monotonic']=110.0;probe['host_boot_id']=str(uuid.uuid4())
        with self.assertRaises(G.ReceiptError):
            G.accept_with_clock(record,probe,operation_id=OPERATION,
                                worker_digest=DIGEST,boot_id=BOOT,
                                start_sample=start,probe_sample=probe_time,now_sample=now)

    def test_bad_shape_uuid_bounds_and_no_native_linux_fallback(self):
        for change in (lambda x:x[0].update(extra=1),
                       lambda x:x[0].update(boot_id='not-a-uuid'),
                       lambda x:x[1].update(continuous_ns='110'),
                       lambda x:x[0].update(awake_ns=-1),
                       lambda x:x[2].update(boot_id=HOST_BOOT.upper())):
            values=list(samples());change(values)
            with self.subTest(change=change),self.assertRaises(H.ClockError):H.validate(*values)
        with patch.object(H.platform,'system',return_value='Linux'):
            with self.assertRaises(H.ClockError):H.sample()

    @unittest.skipUnless(platform.system()=='Darwin','native host clock smoke requires macOS')
    def test_native_mach_pair_and_boot_session_read_only(self):
        first=H.sample();second=H.sample()
        self.assertEqual(H.validate(first,second,second)['host_boot_id'],first['boot_id'])
        self.assertLessEqual(second['continuous_ns']-first['continuous_ns'],H.MAX_WINDOW_NS)


if __name__=='__main__':unittest.main()
