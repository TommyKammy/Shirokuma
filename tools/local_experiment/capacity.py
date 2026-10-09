"""Read-only capacity observations, not permission to resume a retained writer.

Only the owned 0700 scope is measured. Each regular entry is charged the larger
of its logical size and allocated blocks, including repeated hard-link entries.
Limits are observations (not exceptions); unsafe or unreadable inputs fail closed.
"""
from __future__ import annotations

import os
from pathlib import Path
import stat
from typing import TypedDict

GIB = 1024 ** 3
HARD_CAP_BYTES = 8 * GIB
MIN_FREE_BYTES = 16 * GIB
BULK_CAP_BYTES = 7 * GIB
BULK_MIN_FREE_BYTES = 17 * GIB


class Observation(TypedDict):
    used_bytes: int
    free_bytes: int
    hard_limit_exceeded: bool
    bulk_writes_allowed: bool


class CapacityError(RuntimeError):
    """Ownership, type or scope identity could not be established."""


def _owned(st):
    if st.st_uid != os.getuid() or not (stat.S_ISDIR(st.st_mode) or stat.S_ISREG(st.st_mode)):
        raise CapacityError('CAPACITY_INPUT_UNSAFE')


def _measure(fd):
    total = 0
    with os.scandir(fd) as entries:
        for entry in entries:
            try:
                st = os.stat(entry.name, dir_fd=fd, follow_symlinks=False)
            except FileNotFoundError:
                # Active Go writers remove temporary entries. Only a vanished
                # child is tolerable; denied enumeration/stat never counts as zero.
                continue
            _owned(st)
            if stat.S_ISREG(st.st_mode):
                total += max(st.st_size, st.st_blocks * 512)
            else:
                try:
                    child = os.open(entry.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                except FileNotFoundError:
                    continue
                try:
                    opened = os.fstat(child)
                    _owned(opened)
                    if (opened.st_dev, opened.st_ino) != (st.st_dev, st.st_ino):
                        raise CapacityError('CAPACITY_INPUT_CHANGED')
                    total += _measure(child)
                finally:
                    os.close(child)
    return total


def observe(path: Path | str) -> Observation:
    """Observe without writing/chmod, cleanup, or a soft-limit exception.

    At exactly 8 GiB (or below 16 GiB free) the hard guard is reached. Bulk
    writes require strictly below 7 GiB and at least 17 GiB free, retaining
    the existing writer margin. This snapshot is not a reservation or admission.
    """
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        st = os.fstat(fd)
        _owned(st)
        if stat.S_IMODE(st.st_mode) != 0o700:
            raise CapacityError('CAPACITY_SCOPE_UNSAFE')
        used = _measure(fd)
        filesystem = os.fstatvfs(fd)
        free = filesystem.f_bavail * filesystem.f_frsize
        return Observation(used_bytes=used, free_bytes=free,
            hard_limit_exceeded=used >= HARD_CAP_BYTES or free < MIN_FREE_BYTES,
            bulk_writes_allowed=used < BULK_CAP_BYTES and free >= BULK_MIN_FREE_BYTES)
    finally:
        os.close(fd)
