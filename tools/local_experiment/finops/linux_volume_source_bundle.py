"""Offline private source-byte correlation; never runs or transfers a worker.

An exact local source digest is not VM executable attestation. The future VM
runner must bind the bytes it imports/executes to an authenticated receipt.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import stat

SOURCES = ('linux_openat2.py', 'linux_volume_worker.py', 'linux_volume_supervisor.py')
MAX_SOURCE = 64 * 1024
FLAGS = os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK
DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC


class BundleError(ValueError):
    pass


def require(ok):
    if not ok:raise BundleError('SOURCE_UNVERIFIED')


def _identity(info):
    return (info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_gid,
            info.st_nlink, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def _directory(root):
    require(isinstance(root,Path) and root.is_absolute() and '..' not in root.parts and
            str(root).startswith('/') and not str(root).startswith('//'))
    fd=os.open('/',DIR_FLAGS)
    try:
        for name in root.parts[1:]:
            parent=os.fstat(fd)
            require(stat.S_ISDIR(parent.st_mode) and parent.st_uid in (0,os.getuid()) and
                    (stat.S_IMODE(parent.st_mode)&0o022==0 or
                     parent.st_uid==0 and parent.st_mode&stat.S_ISVTX!=0))
            next_fd=os.open(name,DIR_FLAGS,dir_fd=fd)
            os.close(fd);fd=next_fd
        info=os.fstat(fd)
        require(stat.S_ISDIR(info.st_mode) and info.st_uid==os.getuid() and
                stat.S_IMODE(info.st_mode)&0o022==0 and
                _identity(info)==_identity(root.lstat()))
        return fd
    except BaseException:
        os.close(fd);raise


def _read(dirfd,name,device):
    fd=os.open(name,FLAGS,dir_fd=dirfd)
    try:
        before=os.fstat(fd)
        require(stat.S_ISREG(before.st_mode) and before.st_dev==device and
                before.st_uid==os.getuid() and before.st_nlink==1 and
                stat.S_IMODE(before.st_mode)&0o022==0 and 0<before.st_size<=MAX_SOURCE)
        raw=os.read(fd,MAX_SOURCE+1)
        after=os.fstat(fd)
        require(_identity(before)==_identity(after) and len(raw)==before.st_size)
        return _identity(after),hashlib.sha256(raw).hexdigest(),len(raw)
    finally:os.close(fd)


def pin(root):
    """Return a bounded local-only manifest after two identical no-follow passes."""
    try:
        dirfd=_directory(root)
        try:
            parent=_identity(os.fstat(dirfd))
            device=parent[0]
            passes=[]
            for _ in range(2):
                files=tuple((name,*_read(dirfd,name,device)) for name in SOURCES)
                require(_identity(os.fstat(dirfd))==parent and
                        _identity(root.lstat())==parent)
                passes.append(files)
            require(passes[0]==passes[1])
            manifest=tuple((name,size,digest) for name,_,digest,size in passes[0])
            # Domain and fixed names/lengths prevent ambiguous concatenation.
            digest=hashlib.sha256(b'shirokuma-finops-source-bundle-v1\0')
            for name,size,checksum in manifest:
                digest.update(name.encode('ascii')+b'\0'+str(size).encode('ascii')+b'\0'+bytes.fromhex(checksum))
            return {'bundle_digest':digest.hexdigest(),
                    'files':[{'name':name,'size':size,'sha256':checksum}
                             for name,size,checksum in manifest]}
        finally:os.close(dirfd)
    except (OSError,BundleError):
        raise BundleError('SOURCE_UNVERIFIED') from None
