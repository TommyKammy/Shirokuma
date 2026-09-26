"""Private strict SSH argument contract. Does not enroll or connect to a VM.

The existing dedicated VM server key must be independently trusted *before*
creating the exact known-hosts file. A file generated from an untrusted
`ssh-keyscan` or a first-contact VM response is not enrollment evidence.
"""
from __future__ import annotations

import base64
import os
from pathlib import Path
import re
import stat

ALIAS = 'shirokuma-finops-mac-studio-solo'


class EndpointUntrusted(ValueError):
    pass


def require(ok):
    if not ok:
        raise EndpointUntrusted('ENDPOINT_UNTRUSTED')


def _private_file(path, *, secret):
    require(isinstance(path,Path) and path.is_absolute() and
            re.fullmatch(r'/[A-Za-z0-9._/-]+',str(path)) is not None and
            '//' not in str(path) and all(part not in ('.','..') for part in path.parts[1:]))
    try:
        for ancestor in path.parents:
            info=ancestor.lstat()
            require(stat.S_ISDIR(info.st_mode) and info.st_uid in (0,os.getuid()) and
                    (stat.S_IMODE(info.st_mode)&0o022==0 or
                     info.st_uid==0 and info.st_mode&stat.S_ISVTX!=0))
        fd=os.open(path,os.O_RDONLY|os.O_CLOEXEC|os.O_NOFOLLOW|os.O_NONBLOCK)
        try:
            info=os.fstat(fd)
            require(stat.S_ISREG(info.st_mode) and info.st_uid==os.getuid() and
                    info.st_nlink==1 and stat.S_IMODE(info.st_mode) in
                    ((0o600,0o400) if secret else (0o600,0o400,0o644)))
            return os.read(fd,1025) if not secret else None
        finally:os.close(fd)
    except OSError:
        raise EndpointUntrusted('ENDPOINT_UNTRUSTED') from None


def _key(value):
    require(type(value) is str and re.fullmatch(r'[A-Za-z0-9+/]+={0,2}',value) is not None and
            len(value)<=512)
    try:raw=base64.b64decode(value,validate=True)
    except ValueError:
        raise EndpointUntrusted('ENDPOINT_UNTRUSTED') from None
    require(len(raw)>=15 and int.from_bytes(raw[:4],'big')==11 and
            raw[4:15]==b'ssh-ed25519' and len(raw)==4+11+4+32 and
            int.from_bytes(raw[15:19],'big')==32)
    return value


def strict_ssh_prefix(*, port, user, known_hosts, identity_file, enrolled_key):
    """Build, but never execute, an SSH prefix pinned to prior key enrollment."""
    require(type(port) is int and 1<=port<=65535 and
            type(user) is str and re.fullmatch(r'[a-z_][a-z0-9_-]{0,31}',user) is not None)
    _key(enrolled_key)
    raw=_private_file(known_hosts,secret=False)
    _private_file(identity_file,secret=True)
    require(len(raw)<=1024 and raw==f'{ALIAS} ssh-ed25519 {enrolled_key}\n'.encode())
    return ['ssh','-F','/dev/null','-p',str(port),'-i',str(identity_file),
            '-o','StrictHostKeyChecking=yes',
            '-o','UserKnownHostsFile='+str(known_hosts),
            '-o','GlobalKnownHostsFile=/dev/null',
            '-o','HostKeyAlias='+ALIAS,
            '-o','UpdateHostKeys=no',
            '-o','BatchMode=yes',
            '-o','IdentitiesOnly=yes',
            '-o','IdentityAgent=none',
            '-o','PreferredAuthentications=publickey',
            '-o','PasswordAuthentication=no',
            '-o','KbdInteractiveAuthentication=no',
            '-o','ControlMaster=no',
            '-o','ProxyCommand=none',
            '-o','ProxyJump=none',
            '-o','ClearAllForwardings=yes',
            '-o','ForwardAgent=no',
            '-o','RequestTTY=no',
            '-o','ConnectTimeout=3',
            '-o','ConnectionAttempts=1',
            user+'@127.0.0.1']
