"""Private VM-side fixture worker supervisor; not a retained-volume runner.

An SSH client exit is not a cleanup receipt. This module records one operation
outside Docker storage and returns bytes only after reaping its exact child.
The host must independently verify the terminal record after disconnection.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import platform
import re
import signal
import stat
import threading
import time
import uuid

MAX_RESULT = 4096
SCAN_DEADLINE = 10
TERM_AT = 12
KILL_AT = 14
END_AT = 16


class SupervisorError(ValueError):
    pass


def require(ok, reason='SCOPE_INVALID'):
    if not ok:
        raise SupervisorError(reason)


def _json(value):
    return json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False).encode()


def _record_dir(path):
    require(isinstance(path, Path) and path.is_absolute() and not path.is_symlink())
    fd=os.open(path,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW|os.O_CLOEXEC)
    try:
        info=os.fstat(fd)
        require(stat.S_ISDIR(info.st_mode) and stat.S_IMODE(info.st_mode)==0o700 and
                info.st_uid==os.getuid())
        return fd
    except BaseException:
        os.close(fd);raise


def _write(dirfd, name, value, *, initial=False):
    raw=_json(value)
    require(len(raw)<=MAX_RESULT)
    temporary=name if initial else name+'.next'
    fd=os.open(temporary,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW|os.O_CLOEXEC,
               0o600,dir_fd=dirfd)
    try:
        require(os.write(fd,raw)==len(raw), 'READ_FAILED')
        os.fsync(fd)
        os.close(fd)
        fd=None
        if not initial:
            os.replace(temporary,name,src_dir_fd=dirfd,dst_dir_fd=dirfd)
    except BaseException:
        if fd is not None:os.close(fd)
        # Only the exact O_EXCL file created by this invocation is removed.
        # Preserve the preceding record as conservative, nonterminal evidence.
        try:os.unlink(temporary,dir_fd=dirfd)
        except OSError:pass
        raise


def _process_start(pid):
    raw=Path(f'/proc/{pid}/stat').read_text()
    suffix=raw.rsplit(') ',1)
    require(len(suffix)==2 and len(raw)<=4096)
    value=suffix[1].split()[19]  # /proc stat field 22, after pid+comm.
    require(value.isascii() and value.isdecimal())
    return value


def _result(value):
    require(type(value) is dict and set(value)=={
        'logical_regular_file_bytes','regular_file_count','directory_count'})
    size=value['logical_regular_file_bytes']
    require(type(size) is str and size.isascii() and size.isdecimal() and
            str(int(size))==size and int(size)<=2**63-1)
    require(all(type(value[key]) is int and 0<=value[key]<=10_000
                for key in ('regular_file_count','directory_count')))
    return value


def verified_receipt(record_dir, *, operation_id, worker_digest, boot_id):
    """Exact-operation acknowledgement; a missing/nonterminal record is unknown."""
    try:
        require(str(uuid.UUID(operation_id))==operation_id and
                type(worker_digest) is str and re.fullmatch(r'[0-9a-f]{64}',worker_digest) and
                type(boot_id) is str and len(boot_id)==36)
        directory=_record_dir(record_dir)
        try:
            fd=os.open(operation_id+'.json',os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC,dir_fd=directory)
            try:
                info=os.fstat(fd)
                require(stat.S_ISREG(info.st_mode) and info.st_uid==os.getuid() and
                        stat.S_IMODE(info.st_mode)==0o600 and info.st_nlink==1 and
                        0 < info.st_size <= MAX_RESULT)
                raw=os.read(fd,MAX_RESULT+1)
                require(len(raw)==info.st_size)
            finally:os.close(fd)
        finally:os.close(directory)
        value=json.loads(raw)
        require(type(value) is dict and value.get('operation_id')==operation_id and
                value.get('worker_digest')==worker_digest and value.get('boot_id')==boot_id and
                value.get('state') in ('complete','unknown') and value.get('cleanup_verified') is True and
                type(value.get('child_pid')) is int and value['child_pid']>0 and
                type(value.get('child_start')) is str and value['child_start'].isdecimal())
        if value['state']=='complete':
            require(value.get('reason') is None)
            return _result(value.get('result'))
        require(value.get('result') is None)
        raise SupervisorError('READ_FAILED')
    except (OSError,ValueError,TypeError):
        raise SupervisorError('CLEANUP_UNVERIFIED') from None


def run_fixture(worker, *, operation_id, worker_digest, record_dir, clock=time.monotonic):
    """Run one no-subprocess fixture callable with an independent VM deadline."""
    require(platform.system()=='Linux' and callable(worker), 'UNSUPPORTED_PLATFORM')
    require(signal.getsignal(signal.SIGCHLD) is signal.SIG_DFL and
            threading.active_count()==1, 'SCOPE_INVALID')
    try:
        parsed=uuid.UUID(operation_id)
    except (ValueError,TypeError,AttributeError):
        raise SupervisorError('SCOPE_INVALID') from None
    require(parsed.version==4 and str(parsed)==operation_id and
            type(worker_digest) is str and re.fullmatch(r'[0-9a-f]{64}',worker_digest) is not None)
    boot=Path('/proc/sys/kernel/random/boot_id').read_text().strip()
    require(type(boot) is str and len(boot)==36)
    directory=_record_dir(record_dir)
    name=operation_id+'.json'
    original_hup=signal.getsignal(signal.SIGHUP)
    reader=writer=None
    gate_reader=gate_writer=None
    child=None
    reaped=False
    record_written=False
    terminal_written=False
    child_start=None
    started=clock()
    try:
        _write(directory,name,{'operation_id':operation_id,'worker_digest':worker_digest,
               'boot_id':boot,'state':'registered','cleanup_verified':False},initial=True)
        record_written=True
        reader,writer=os.pipe2(os.O_CLOEXEC)
        gate_reader,gate_writer=os.pipe2(os.O_CLOEXEC)
        signal.signal(signal.SIGHUP,signal.SIG_IGN)
        parent_pid=os.getpid()
        child=os.fork()
        if child==0:
            try:
                os.close(reader);os.close(gate_writer)
                # Parent-death signal prevents a surviving worker if the
                # supervisor disappears; check the fork/prctl race explicitly.
                import ctypes
                libc=ctypes.CDLL(None,use_errno=True)
                if libc.prctl(1,signal.SIGKILL,0,0,0)!=0 or os.getppid()!=parent_pid:
                    os._exit(2)
                if os.read(gate_reader,1)!=b'1':os._exit(2)
                os.close(gate_reader)
                result=_json(_result(worker()))
                if len(result)>MAX_RESULT:os._exit(2)
                os.write(writer,result)
                os._exit(0)
            except BaseException:
                os._exit(2)
        os.close(writer);writer=None
        os.close(gate_reader);gate_reader=None
        child_start=_process_start(child)
        _write(directory,name,{'operation_id':operation_id,'worker_digest':worker_digest,
               'boot_id':boot,'state':'running','child_pid':child,
               'child_start':child_start,'cleanup_verified':False})
        os.write(gate_writer,b'1');os.close(gate_writer);gate_writer=None
        status=None
        while clock()-started < END_AT:
            found,value=os.waitpid(child,os.WNOHANG)
            if found==child:
                status=value;reaped=True;break
            elapsed=clock()-started
            if elapsed>=KILL_AT:
                try:os.kill(child,signal.SIGKILL)
                except ProcessLookupError:pass
            elif elapsed>=TERM_AT:
                try:os.kill(child,signal.SIGTERM)
                except ProcessLookupError:pass
            time.sleep(0.01)
        if status is None:
            # A D-state child or failed waitpid remains unresolved. Attempt a
            # bounded exact-child reap; never promote a late result to success.
            try:os.kill(child,signal.SIGKILL)
            except ProcessLookupError:pass
            until=clock()+2
            while clock()<until:
                found,value=os.waitpid(child,os.WNOHANG)
                if found==child:
                    status=value;reaped=True;break
                time.sleep(0.01)
            receipt={'operation_id':operation_id,'worker_digest':worker_digest,'boot_id':boot,
                     'state':'unknown','reason':'READ_TIMEOUT' if reaped else 'CLEANUP_UNVERIFIED',
                     'child_pid':child,'child_start':child_start,'cleanup_verified':reaped,'result':None}
            _write(directory,name,receipt)
            terminal_written=True
            return receipt
        os.set_blocking(reader,False)
        output=bytearray()
        while len(output)<=MAX_RESULT:
            try:part=os.read(reader,MAX_RESULT+1-len(output))
            except BlockingIOError:break
            if not part:break
            output.extend(part)
        accepted=None
        if (os.WIFEXITED(status) and os.WEXITSTATUS(status)==0 and
                clock()-started<=SCAN_DEADLINE and len(output)<=MAX_RESULT):
            try:accepted=_result(json.loads(output))
            except (ValueError,TypeError):pass
        receipt={'operation_id':operation_id,'worker_digest':worker_digest,'boot_id':boot,
                 'state':'complete' if accepted is not None else 'unknown',
                 'reason':None if accepted is not None else 'READ_FAILED',
                 'child_pid':child,'child_start':child_start,'cleanup_verified':True,
                 'result':accepted}
        _write(directory,name,receipt)
        terminal_written=True
        return receipt
    finally:
        signal.signal(signal.SIGHUP,original_hup)
        if gate_reader is not None:os.close(gate_reader)
        if gate_writer is not None:os.close(gate_writer)
        if child is not None and child>0 and not reaped:
            # This targets only the forked worker. A failed bounded reap is
            # never converted into a successful cleanup acknowledgement.
            try:os.kill(child,signal.SIGKILL)
            except ProcessLookupError:pass
            deadline=clock()+2
            while clock()<deadline:
                try:
                    found,_=os.waitpid(child,os.WNOHANG)
                    if found==child:
                        reaped=True;break
                except ChildProcessError:break  # Unexpected ECHILD is not a cleanup receipt.
                time.sleep(0.01)
        if child is not None and child>0 and record_written and not terminal_written:
            try:
                _write(directory,name,{'operation_id':operation_id,'worker_digest':worker_digest,
                    'boot_id':boot,'state':'unknown','reason':
                    'READ_FAILED' if reaped else 'CLEANUP_UNVERIFIED',
                    'child_pid':child,'child_start':child_start,
                    'cleanup_verified':reaped,'result':None})
            except Exception:pass  # Host must reject a missing/nonterminal receipt.
        if writer is not None:os.close(writer)
        if reader is not None:os.close(reader)
        os.close(directory)
