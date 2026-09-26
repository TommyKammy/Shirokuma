"""Bounded metadata-only logical regular-file sizes for a verified directory."""
from __future__ import annotations

import os
import errno
from pathlib import Path
import stat
import time

MAX_ENTRIES = 10_000
MAX_DEPTH = 32
MAX_NAME_BYTES = 4 * 1024 * 1024
MAX_SECONDS = 10
MAX_BYTES = 2**63-1
FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW


class UsageError(ValueError): pass


def require(ok, reason='SCOPE_INVALID'):
    if not ok: raise UsageError(reason)


def identity(s):
    return (s.st_mode, s.st_dev, s.st_ino, s.st_uid, s.st_gid, s.st_nlink,
            s.st_size, s.st_mtime_ns, s.st_ctime_ns)


def _check(deadline):
    require(time.monotonic() < deadline, 'READ_TIMEOUT')


def _root(root, device, inode):
    require(isinstance(root, Path) and root.is_absolute() and '..' not in root.parts)
    # Open every ancestor relative to its verified descriptor. O_NOFOLLOW on
    # the final pathname alone would not protect against an ancestor swap.
    fd = os.open('/', FLAGS)
    try:
        for part in root.parts[1:]:
            try: next_fd = os.open(part, FLAGS, dir_fd=fd)
            except OSError as error:
                if error.errno in (errno.ELOOP, errno.ENOTDIR): raise UsageError('SCOPE_INVALID') from None
                raise
            os.close(fd);fd = next_fd
        s = os.fstat(fd); path = root.lstat()
        require(identity(s) == identity(path) and stat.S_ISDIR(s.st_mode) and
                s.st_uid == os.getuid() and stat.S_IMODE(s.st_mode) == 0o700 and
                s.st_dev == device)
        require(s.st_ino == inode, 'IDENTITY_CHANGED')
        return fd
    except BaseException:
        os.close(fd); raise


def _pass(root, device, deadline, root_fd, required_root_entries=()):
    entries = {}; names_bytes = files = dirs = total = 0
    def walk(fd, relative, depth):
        nonlocal names_bytes, files, dirs, total
        _check(deadline)
        before = identity(os.fstat(fd))
        require(stat.S_ISDIR(before[0]) and before[1] == device and before[3] == os.getuid())
        with os.scandir(fd) as stream:
            for item in stream:
                _check(deadline)
                name = item.name
                require(type(name) is str and name not in ('.','..') and '/' not in name)
                names_bytes += len(os.fsencode(name))
                require(len(entries) < MAX_ENTRIES and names_bytes <= MAX_NAME_BYTES, 'READ_LIMIT')
                path = relative + (name,)
                first = os.stat(name, dir_fd=fd, follow_symlinks=False)
                require(first.st_dev == device and first.st_uid == os.getuid())
                if stat.S_ISREG(first.st_mode): require(first.st_nlink == 1)
                if stat.S_ISREG(first.st_mode):
                    files += 1;total += first.st_size
                    require(0 <= first.st_size <= MAX_BYTES and total <= MAX_BYTES, 'READ_LIMIT')
                elif stat.S_ISDIR(first.st_mode):
                    require(depth < MAX_DEPTH, 'READ_LIMIT')
                    dirs += 1
                else: raise UsageError('SCOPE_INVALID')
                entries[path] = identity(first)
                if stat.S_ISDIR(first.st_mode):
                    child = os.open(name, FLAGS, dir_fd=fd)
                    try:
                        require(identity(os.fstat(child)) == identity(first), 'TREE_CHANGED')
                        walk(child, path, depth + 1)
                    finally: os.close(child)
                require(identity(os.stat(name, dir_fd=fd, follow_symlinks=False)) == identity(first),
                        'TREE_CHANGED')
                _check(deadline)
        require(identity(os.fstat(fd)) == before, 'TREE_CHANGED')
        _check(deadline)
    fd = os.dup(root_fd)
    try:
        root_before = identity(os.fstat(fd))
        walk(fd, (), 0)
        for name, kind in required_root_entries:
            info = entries.get((name,))
            require(info is not None and (stat.S_ISREG(info[0]) if kind == 'file'
                                           else stat.S_ISDIR(info[0])))
        require(identity(os.fstat(fd)) == root_before and identity(root.lstat()) == root_before,
                'TREE_CHANGED')
        _check(deadline)
        return (root_before, entries, total, files, dirs)
    finally: os.close(fd)


def measure(root, *, device, inode, required_root_entries=()):
    require(type(required_root_entries) is tuple and all(
        type(name) is str and name not in ('', '.', '..') and '/' not in name and
        kind in ('file', 'directory') for name, kind in required_root_entries))
    deadline = time.monotonic() + MAX_SECONDS
    fd = _root(root, device, inode)
    try:
        first = _pass(root, device, deadline, fd, required_root_entries)
        second = _pass(root, device, deadline, fd, required_root_entries)
        require(first == second, 'TREE_CHANGED')
        _check(deadline)
        require(identity(os.fstat(fd)) == first[0] and identity(root.lstat()) == first[0], 'TREE_CHANGED')
        return dict(used_bytes=str(first[2]),regular_file_count=first[3],directory_count=first[4])
    finally: os.close(fd)
