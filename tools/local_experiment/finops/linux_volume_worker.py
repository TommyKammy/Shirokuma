"""Private Linux-only no-atime metadata scanner; not wired to retained volumes.

The caller must establish Docker identity, writer quiescence, root ownership,
mount topology and remote cleanup before any future production integration.
This module deliberately has no CLI and does not create/mount a volume.
"""
from __future__ import annotations

import fcntl
import errno
import os
from pathlib import Path
import platform
import re
import stat
import time

from tools.local_experiment.finops import linux_openat2 as AT

MAX_ENTRIES = 10_000
MAX_DEPTH = 32
MAX_NAMES = 4 * 1024 * 1024
MAX_BYTES = 2**63 - 1
MAX_SECONDS = 10
MAX_MOUNTINFO = 1024 * 1024


class ScanError(ValueError):
    pass


def require(condition, reason='SCOPE_INVALID'):
    if not condition:
        raise ScanError(reason)


def _native():
    require(platform.system() == 'Linux' and
            all(hasattr(os, name) for name in ('O_PATH', 'O_NOATIME', 'O_NOFOLLOW', 'O_CLOEXEC')),
            'UNSUPPORTED_PLATFORM')


def _flags():
    return os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_NOATIME | os.O_CLOEXEC


def _check(deadline):
    require(time.monotonic() < deadline, 'READ_TIMEOUT')


def _identity(info):
    return (info.st_mode, info.st_dev, info.st_ino, info.st_uid, info.st_gid,
            info.st_nlink, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def _mountpoints(raw):
    require(type(raw) is str and len(raw.encode('utf-8')) <= MAX_MOUNTINFO, 'READ_LIMIT')
    # procfs mountinfo is LF-framed. splitlines() silently accepts truncated
    # records, CRLF and Unicode line separators as independent mount entries.
    require(raw.endswith('\n') and '\r' not in raw)
    lines = raw.split('\n')[:-1]
    require(all(line and line.splitlines() == [line] for line in lines))
    result = set()
    for line in lines:
        fields = line.split(' ')
        require(len(fields) >= 10 and fields[0].isdigit() and fields[4].startswith('/'))
        # mountinfo escapes spaces, tabs, newlines and backslashes as octal.
        encoded = fields[4]
        require(re.fullmatch(r'(?:[^\\]|\\(?:040|011|012|134))*', encoded) is not None)
        value = re.sub(r'\\(040|011|012|134)',
                       lambda match: chr(int(match[1], 8)), encoded)
        # Topology comparison uses lexical paths; do not let a noncanonical
        # mountpoint alias hide a mount underneath the measured root.
        require(value.startswith('/') and not value.startswith('//') and
                os.path.normpath(value) == value)
        result.add(value)
    require(result)
    return result


def _topology(root, mountpoints):
    path = str(root)
    require(path.startswith('/') and not path.startswith('//') and os.path.normpath(path) == path)
    require(not any(mount == path or mount.startswith(path + '/') for mount in mountpoints), 'MOUNT_CHANGED')


def _read_mountinfo():
    with open('/proc/self/mountinfo', 'rb', buffering=0) as source:
        raw = source.read(MAX_MOUNTINFO + 1)
    require(len(raw) <= MAX_MOUNTINFO, 'READ_LIMIT')
    return raw.decode('utf-8')


def _mount_id(fd):
    with open(f'/proc/self/fdinfo/{fd}', 'rb', buffering=0) as source:
        raw=source.read(4097)
    require(len(raw) <= 4096, 'READ_LIMIT')
    return _parse_mount_id(raw)


def _parse_mount_id(raw):
    # Do not let \s* consume a newline and borrow an ID from another field.
    require(type(raw) is bytes and raw.endswith(b'\n') and b'\r' not in raw)
    lines = raw.split(b'\n')[:-1]
    require(all(line for line in lines))
    matches = [line[len(b'mnt_id:\t'):] for line in lines if line.startswith(b'mnt_id:')]
    require(len(matches) == 1 and re.fullmatch(rb'[0-9]+', matches[0]) is not None and
            len(matches[0]) <= 19)
    value = int(matches[0])
    require(0 < value <= 2**63 - 1)
    return value


def _guarded(fd, relative, flags):
    try:
        return AT.open_beneath(fd, relative, flags)
    except OSError as error:
        reason = ('MOUNT_CHANGED' if error.errno == errno.EXDEV else
                  'UNSUPPORTED_PLATFORM' if error.errno in (errno.ENOSYS, errno.EINVAL) else
                  'NOATIME_UNAVAILABLE' if flags & os.O_NOATIME and error.errno in
                  (errno.EPERM, errno.EACCES) else 'SCOPE_INVALID')
        raise ScanError(reason) from None


def _open_anchor(anchor, device, inode, mount_id):
    # Explicit trusted filesystem anchor: mount crossings above this point
    # are verified by the caller, not by the worker. O_PATH doesn't enumerate.
    fd = os.open('/', os.O_PATH | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        for part in anchor.parts[1:]:
            next_fd = os.open(part, os.O_PATH | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=fd)
            os.close(fd);fd = next_fd
        info=os.fstat(fd)
        require(stat.S_ISDIR(info.st_mode) and (info.st_dev,info.st_ino)==(device,inode) and
                _mount_id(fd)==mount_id,
                'IDENTITY_CHANGED')
        return fd
    except BaseException:
        os.close(fd)
        raise


def _open_root(anchor_fd, relative, device, inode, allowed_uids, allowed_modes):
    fd = _guarded(anchor_fd, relative, _flags())
    try:
        require(fcntl.fcntl(fd, fcntl.F_GETFL) & os.O_NOATIME, 'NOATIME_UNAVAILABLE')
        info = os.fstat(fd)
        require(stat.S_ISDIR(info.st_mode) and (info.st_dev, info.st_ino) == (device, inode) and
                info.st_uid in allowed_uids and stat.S_IMODE(info.st_mode) in allowed_modes,
                'IDENTITY_CHANGED')
        return fd
    except BaseException:
        os.close(fd)
        raise


def _pass(pinned, device, allowed_uids, allowed_modes, deadline):
    entries = {}; names = files = directories = total = 0

    def walk(fd, relative, depth):
        nonlocal names, files, directories, total
        _check(deadline)
        before = _identity(os.fstat(fd))
        require(stat.S_ISDIR(before[0]) and before[1] == device and before[3] in allowed_uids and
                stat.S_IMODE(before[0]) in allowed_modes)
        with os.scandir(fd) as iterator:
            for entry in iterator:
                _check(deadline)
                name = entry.name
                require(type(name) is str and name not in ('', '.', '..') and '/' not in name)
                names += len(os.fsencode(name))
                require(len(entries) < MAX_ENTRIES and names <= MAX_NAMES, 'READ_LIMIT')
                path = relative + (name,)
                root_relative='/'.join(relative+(name,))
                item = _guarded(pinned, root_relative, os.O_PATH | os.O_CLOEXEC)
                try:
                    info = os.fstat(item)
                finally:
                    os.close(item)
                require(info.st_dev == device and info.st_uid in allowed_uids)
                if stat.S_ISREG(info.st_mode):
                    require(info.st_nlink == 1)
                    files += 1;total += info.st_size
                    require(0 <= info.st_size <= MAX_BYTES and total <= MAX_BYTES, 'READ_LIMIT')
                elif stat.S_ISDIR(info.st_mode):
                    require(depth < MAX_DEPTH and stat.S_IMODE(info.st_mode) in allowed_modes,
                            'READ_LIMIT' if depth >= MAX_DEPTH else 'SCOPE_INVALID')
                    directories += 1
                else:
                    raise ScanError('SCOPE_INVALID')
                first = _identity(info)
                entries[path] = first
                if stat.S_ISDIR(info.st_mode):
                    child = _guarded(pinned, root_relative, _flags())
                    try:
                        require(fcntl.fcntl(child, fcntl.F_GETFL) & os.O_NOATIME, 'NOATIME_UNAVAILABLE')
                        require(_identity(os.fstat(child)) == first, 'TREE_CHANGED')
                        walk(child, path, depth + 1)
                    finally:
                        os.close(child)
                item = _guarded(pinned, root_relative, os.O_PATH | os.O_CLOEXEC)
                try:
                    require(_identity(os.fstat(item)) == first, 'TREE_CHANGED')
                finally:
                    os.close(item)
                _check(deadline)
        require(_identity(os.fstat(fd)) == before, 'TREE_CHANGED')
        _check(deadline)

    # Reopen '.' rather than dup(): dup shares the directory offset and could
    # make the second pass appear empty after the first enumeration.
    fd = _guarded(pinned, '.', _flags())
    try:
        require(fcntl.fcntl(fd, fcntl.F_GETFL) & os.O_NOATIME, 'NOATIME_UNAVAILABLE')
        root_before = _identity(os.fstat(fd))
        walk(fd, (), 0)
        require(_identity(os.fstat(fd)) == root_before, 'TREE_CHANGED')
        return root_before, entries, total, files, directories
    finally:
        os.close(fd)


def measure(root, *, anchor, anchor_device, anchor_inode, anchor_mount_id, device, inode,
            allowed_uids, allowed_modes, mountinfo=None):
    """Return private aggregate bytes/counts, never a public FinOps report row."""
    _native()
    require(isinstance(root, Path) and root.is_absolute() and '..' not in root.parts and
            str(root).startswith('/') and not str(root).startswith('//') and
            os.path.normpath(str(root)) == str(root) and
            isinstance(anchor, Path) and anchor.is_absolute() and '..' not in anchor.parts and
            str(anchor).startswith('/') and not str(anchor).startswith('//') and
            os.path.normpath(str(anchor)) == str(anchor) and
            type(anchor_device) is int and anchor_device > 0 and
            type(anchor_inode) is int and anchor_inode > 0 and
            type(anchor_mount_id) is int and anchor_mount_id > 0 and
            type(device) is int and device > 0 and type(inode) is int and inode > 0 and
            type(allowed_uids) is frozenset and allowed_uids and
            all(type(x) is int and x >= 0 for x in allowed_uids) and
            type(allowed_modes) is frozenset and allowed_modes and
            all(type(x) is int and 0 <= x <= 0o777 for x in allowed_modes))
    try:
        relative = root.relative_to(anchor)
    except ValueError:
        raise ScanError('SCOPE_INVALID') from None
    require(relative.parts and str(relative) != '.')
    deadline = time.monotonic() + MAX_SECONDS
    # mountinfo injection is exclusively a fixture seam. A future retained
    # volume wrapper must use fresh kernel mountinfo and stronger per-open
    # mount controls; this worker cannot prove absence of transient bind mounts.
    mountpoints = _mountpoints(_read_mountinfo() if mountinfo is None else mountinfo)
    _topology(root, mountpoints)
    trusted = _open_anchor(anchor, anchor_device, anchor_inode, anchor_mount_id)
    try:
        pinned = _open_root(trusted, str(relative), device, inode, allowed_uids, allowed_modes)
        try:
            results = []
            for _ in range(2):
                results.append(_pass(pinned, device, allowed_uids, allowed_modes, deadline))
                _topology(root, _mountpoints(_read_mountinfo() if mountinfo is None else mountinfo))
                verify = _open_root(trusted, str(relative), device, inode, allowed_uids, allowed_modes)
                try:
                    require(_identity(os.fstat(verify)) == _identity(os.fstat(pinned)), 'TREE_CHANGED')
                finally:
                    os.close(verify)
                check_anchor = _open_anchor(anchor, anchor_device, anchor_inode, anchor_mount_id)
                os.close(check_anchor)
            require(results[0] == results[1], 'TREE_CHANGED')
            _check(deadline)
            return {'logical_regular_file_bytes':str(results[0][2]),
                    'regular_file_count':results[0][3], 'directory_count':results[0][4]}
        finally:
            os.close(pinned)
    finally:
        os.close(trusted)
