from pathlib import Path
import tempfile
import unittest
from unittest import mock

from tools.local_experiment import capacity as C
from tools.local_experiment import flux_local_build as B
from tools.local_experiment import flux_refresh_scan as F
from tools.local_experiment import restart_supervisor as S


def observation(used=0, free=30*C.GIB):
    return dict(used_bytes=used, free_bytes=free,
        hard_limit_exceeded=used>=8*C.GIB or free<16*C.GIB,
        bulk_writes_allowed=used<7*C.GIB and free>=17*C.GIB)


class CallerTests(unittest.TestCase):
    def test_bulk_guards_still_stop_at_soft_usage_and_free_bounds(self):
        for value in (observation(7*C.GIB), observation(8*C.GIB),
                      observation(free=17*C.GIB-1), observation(free=16*C.GIB-1)):
            with self.subTest(value=value), mock.patch.object(C, 'observe', return_value=value):
                with self.assertRaisesRegex(RuntimeError, 'BUILD_DISK_HOLD'):
                    B.budget(Path('/unused'))
                with self.assertRaisesRegex(F.F.R.RunnerError, 'REFRESH_DISK_BUDGET_INVALID'):
                    F.check_budget(Path('/unused'))
                self.assertEqual(F.observation(Path('/unused')), value)
        with mock.patch.object(C, 'observe', return_value=observation(7*C.GIB-1, 17*C.GIB)):
            B.budget(Path('/unused'))
            F.check_budget(Path('/unused'))

    def test_explicit_inspection_reserve_keeps_old_bounds(self):
        for reserve in (256*1024**2, 512*1024**2, C.GIB):
            with self.subTest(reserve=reserve):
                limit=8*C.GIB-reserve
                with mock.patch.object(C, 'observe', return_value=observation(limit-1)):
                    B.budget(Path('/unused'), reserve)
                with mock.patch.object(C, 'observe', return_value=observation(limit)):
                    with self.assertRaisesRegex(RuntimeError, 'BUILD_DISK_HOLD'):
                        B.budget(Path('/unused'), reserve)
                with mock.patch.object(C, 'observe', return_value=observation(0, 17*C.GIB-1)):
                    with self.assertRaisesRegex(RuntimeError, 'BUILD_DISK_HOLD'):
                        B.budget(Path('/unused'), reserve)

    def test_refresh_unsafe_observation_is_domain_hold_not_zero_usage(self):
        for error in (C.CapacityError('unsafe'), PermissionError('denied')):
            with mock.patch.object(C, 'observe', side_effect=error):
                with self.assertRaisesRegex(F.F.R.RunnerError, 'REFRESH_CAPACITY_INPUT_UNSAFE'):
                    F.usage(Path('/unused'))

    def test_parent_reviews_over_soft_scopes_without_writes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            value=observation(7*C.GIB+1)
            with mock.patch.object(C, 'observe', return_value=value), \
                 mock.patch.object(S, 'measure', return_value=value['used_bytes']), \
                 mock.patch.object(S.shutil, 'disk_usage', return_value=mock.Mock(free=30*C.GIB)):
                result=S.review_capacity(root)
            self.assertEqual(len(result),2)
            self.assertTrue(all(x['observation']==value for x in result))
            self.assertEqual(list(root.iterdir()),[])

    def test_parent_rejects_lying_incomplete_and_hard_bound_observations(self):
        valid=observation(7*C.GIB+1)
        values=(dict(valid, used_bytes=True), dict(valid, hard_limit_exceeded=0),
                dict(valid, bulk_writes_allowed=0), dict(valid, bulk_writes_allowed=True),
                dict(valid, free_bytes=True), dict(valid, free_bytes=16*C.GIB-1),
                dict(valid, used_bytes=8*C.GIB), dict(valid, extra='authority'),
                {k:v for k,v in valid.items() if k!='free_bytes'})
        for value in values:
            with self.subTest(value=value), mock.patch.object(C, 'observe', return_value=value), \
                 mock.patch.object(S, 'measure', return_value=valid['used_bytes']), \
                 mock.patch.object(S.shutil, 'disk_usage', return_value=mock.Mock(free=30*C.GIB)):
                with self.assertRaisesRegex(S.R.RunnerError, 'CAPACITY_PARENT_REVIEW_FAILED'):
                    S.review_capacity(Path('/unused'))

    def test_parent_independent_measurement_and_current_free_are_required(self):
        value=observation(7*C.GIB+1)
        for used,free in ((value['used_bytes']+1,30*C.GIB),(value['used_bytes'],16*C.GIB-1)):
            with mock.patch.object(C, 'observe', return_value=value), \
                 mock.patch.object(S, 'measure', return_value=used), \
                 mock.patch.object(S.shutil, 'disk_usage', return_value=mock.Mock(free=free)):
                with self.assertRaisesRegex(S.R.RunnerError, 'CAPACITY_PARENT_REVIEW_FAILED'):
                    S.review_capacity(Path('/unused'))

    def test_parent_measure_fails_closed_on_root_and_enumeration(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            root.chmod(0o755)
            with self.assertRaises(S.R.RunnerError):
                S.measure(root)
            root.chmod(0o700)
            def denied(*args, **kwargs):
                kwargs['onerror'](PermissionError('denied'))
                return iter(())
            with mock.patch.object(S.os, 'walk', side_effect=denied):
                with self.assertRaises(PermissionError):
                    S.measure(root)


if __name__ == '__main__':
    unittest.main()
