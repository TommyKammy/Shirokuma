"""VM-local disposable bind test. Run only inside a private mount namespace.

Invocation must already be wrapped by `unshare --user --map-root-user --mount
--propagation private`; never run on a host namespace or retained volume.
"""
from __future__ import annotations

import errno
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

from tools.local_experiment.finops import linux_openat2 as AT


def main():
    # A real private mount namespace must be active; no mount or cleanup is
    # attempted if the launching unshare did not isolate this process.
    if (os.geteuid()!=0 or
            os.readlink('/proc/self/ns/mnt')==os.readlink('/proc/1/ns/mnt') or
            os.readlink('/proc/self/ns/user')==os.readlink('/proc/1/ns/user')):
        raise RuntimeError('private user and mount namespace required')
    base=Path(tempfile.mkdtemp(dir='/tmp',prefix='shirokuma-finops-bind-'))
    identity=(base.stat().st_dev,base.stat().st_ino)
    anchor=base/'anchor';anchor.mkdir(mode=0o700)
    source=base/'source';source.mkdir(mode=0o700)
    directory=anchor/'directory';directory.mkdir(mode=0o700)
    (source/'file').write_bytes(b'fixture')
    file_source=base/'source-file';file_source.write_bytes(b'fixture')
    file_target=anchor/'file';file_target.write_bytes(b'fixture')
    if not (anchor.stat().st_dev==source.stat().st_dev==file_source.stat().st_dev):
        raise RuntimeError('same-device fixture required')
    mounted=[]
    try:
        for origin,target in ((source,directory),(file_source,file_target)):
            subprocess.run(['mount','--bind',str(origin),str(target)],check=True,
                           stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL,timeout=5)
            mounted.append(target)
        fd=os.open(anchor,os.O_PATH|os.O_DIRECTORY|os.O_CLOEXEC)
        try:
            for name in ('directory','file'):
                try:
                    opened=AT.open_beneath(fd,name,os.O_PATH|os.O_CLOEXEC)
                except OSError as error:
                    if error.errno!=errno.EXDEV:
                        raise RuntimeError('bind boundary did not return EXDEV') from error
                else:
                    os.close(opened)
                    raise AssertionError('bind boundary crossed')
        finally:os.close(fd)
        print('same-device directory and file bind mounts: EXDEV')
    finally:
        failures=[]
        for target in reversed(mounted):
            try:subprocess.run(['umount',str(target)],check=True,
                               stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL,timeout=5)
            except Exception:failures.append(target)
        if failures:
            # Never recursively delete a tree with a surviving mount.
            raise RuntimeError('fixture mount cleanup unverified')
        if (base.parent!=Path('/tmp') or not base.name.startswith('shirokuma-finops-bind-') or
                base.is_symlink() or (base.stat().st_dev,base.stat().st_ino)!=identity or
                str(base) in Path('/proc/self/mountinfo').read_text()):
            raise RuntimeError('fixture identity or mount cleanup unverified')
        shutil.rmtree(base)


if __name__=='__main__':main()
