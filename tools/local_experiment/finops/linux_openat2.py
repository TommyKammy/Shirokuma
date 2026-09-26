"""Private Linux openat2 boundary; no syscall fallback to unsafe open/stat."""
from __future__ import annotations

import ctypes
import errno
import os
import platform

SYS_OPENAT2 = 437  # Linux aarch64 and x86_64; reject every other architecture.
RESOLVE_NO_XDEV = 0x01
RESOLVE_NO_SYMLINKS = 0x04
RESOLVE_BENEATH = 0x08
RESOLVE = RESOLVE_NO_XDEV | RESOLVE_NO_SYMLINKS | RESOLVE_BENEATH


class OpenHow(ctypes.Structure):
    _fields_ = [('flags', ctypes.c_uint64), ('mode', ctypes.c_uint64),
                ('resolve', ctypes.c_uint64)]


def open_beneath(directory_fd, relative, flags, *, syscall=None):
    """Open only beneath a trusted anchor without crossing *any* mountpoint."""
    if platform.system() != 'Linux' or platform.machine() not in ('aarch64', 'x86_64'):
        raise OSError(errno.ENOSYS, 'openat2 unavailable')
    if (type(directory_fd) is not int or directory_fd < 0 or
            type(relative) is not str or not relative or relative.startswith('/') or
            '\x00' in relative or any(part in ('', '..') for part in relative.split('/')) or
            type(flags) is not int or flags < 0):
        raise OSError(errno.EINVAL, 'invalid relative open')
    how=OpenHow(flags,0,RESOLVE)
    raw=relative.encode('utf-8',errors='surrogateescape')
    if syscall is None:
        libc=ctypes.CDLL(None,use_errno=True)
        libc.syscall.restype=ctypes.c_long
        result=libc.syscall(SYS_OPENAT2,directory_fd,ctypes.c_char_p(raw),
                            ctypes.byref(how),ctypes.sizeof(how))
        if result == -1:
            code=ctypes.get_errno()
            raise OSError(code, os.strerror(code))
    else:
        result=syscall(SYS_OPENAT2,directory_fd,raw,how)
    if type(result) is not int or result < 0:
        raise OSError(errno.EIO, 'openat2 returned invalid fd')
    return result
