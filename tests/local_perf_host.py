"""A fixed host disk for local performance harness tests.

The capacity protocol keeps a 256 GiB host reserve, so tests that create a
campaign fail on hosts with less free space, such as CI runners. These tests
exercise capacity accounting, not the machine they run on, so they see the same
large disk everywhere. Import setUpModule and tearDownModule into a test module.
"""
import shutil
from collections import namedtuple
from unittest import mock

TB = 10**12
_Usage = namedtuple("usage", "total used free")
_patcher = mock.patch.object(shutil, "disk_usage", return_value=_Usage(4 * TB, 1 * TB, 3 * TB))


def setUpModule():
    _patcher.start()


def tearDownModule():
    _patcher.stop()
