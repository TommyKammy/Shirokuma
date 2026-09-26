"""Versioned private VM receipt/probe wire contract; no network or scan entrypoint.

Parsing a response does not authenticate its sender. A future transport must
enroll and pin the dedicated VM's SSH server key *before* using these fields.
"""
from __future__ import annotations

import json
import re
import uuid

MAX_WIRE = 16 * 1024
MAX_RECORD = 4096
VERSION = 1


class WireError(ValueError):
    pass


def require(ok):
    if not ok:
        raise WireError('RECEIPT_UNVERIFIED')


def _uuid(value):
    require(type(value) is str)
    try:parsed=uuid.UUID(value)
    except ValueError:
        raise WireError('RECEIPT_UNVERIFIED') from None
    require(parsed.version==4 and str(parsed)==value)
    return value


def _digest(value):
    require(type(value) is str and re.fullmatch(r'[0-9a-f]{64}',value) is not None)
    return value


def _tick(value):
    require(type(value) is str and re.fullmatch(r'(?:0|[1-9][0-9]{0,19})',value) is not None)
    return value


def _vm(value):
    require(type(value) is dict and set(value)=={
        'boot_id','pid_namespace','mount_namespace','proc_device','proc_inode','proc_mount_id'})
    _uuid(value['boot_id'])
    for key in ('pid_namespace','mount_namespace','proc_device','proc_inode','proc_mount_id'):
        require(type(value[key]) is int and 0 < value[key] <= 2**63-1)
    return value


def _unique(items):
    result={}
    for key,value in items:
        require(key not in result)
        result[key]=value
    return result


def _decode(raw, limit):
    require(type(raw) is bytes and 0 < len(raw) <= limit)
    try:
        value=json.loads(raw.decode('utf-8'),object_pairs_hook=_unique,
                         parse_constant=lambda _: (_ for _ in ()).throw(WireError('RECEIPT_UNVERIFIED')),
                         parse_float=lambda _: (_ for _ in ()).throw(WireError('RECEIPT_UNVERIFIED')))
    except (UnicodeError,ValueError,TypeError,RecursionError):
        raise WireError('RECEIPT_UNVERIFIED') from None
    return value


def parse(raw, *, nonce, operation_id, expected_bundle_digest, vm_pin):
    """Validate an already bounded response from an independently trusted VM."""
    _uuid(nonce);_uuid(operation_id);_digest(expected_bundle_digest);_vm(vm_pin)
    value=_decode(raw,MAX_WIRE)
    require(type(value) is dict and set(value)=={
        'version','nonce','bundle_digest','vm_before','vm_after','record','probe'})
    require(type(value['version']) is int and value['version']==VERSION and
            value['nonce']==nonce and value['bundle_digest']==expected_bundle_digest and
            value['vm_before']==vm_pin and value['vm_after']==vm_pin)
    _vm(value['vm_before']);_vm(value['vm_after'])
    record=value['record'];probe=value['probe']
    require(type(record) is dict and type(probe) is dict and
            len(json.dumps(record,sort_keys=True,separators=(',',':')).encode())<=MAX_RECORD and
            set(record)=={'operation_id','worker_digest','boot_id','state','reason',
                          'child_pid','child_start','cleanup_verified','result'} and
            set(probe)=={'pid','start_ticks','pid_namespace','proc_mount_id','query_complete'} and
            record['operation_id']==operation_id and
            record['boot_id']==vm_pin['boot_id'] and
            record['worker_digest']==expected_bundle_digest and
            type(record['child_pid']) is int and record['child_pid']>0 and
            record['state'] in ('complete','unknown') and
            record['cleanup_verified'] is True and
            probe['query_complete'] is True and
            type(probe['pid']) is int and probe['pid']==record['child_pid'] and
            type(probe['pid_namespace']) is int and
            probe['pid_namespace']==vm_pin['pid_namespace'] and
            type(probe['proc_mount_id']) is int and
            probe['proc_mount_id']==vm_pin['proc_mount_id'])
    _tick(record['child_start'])
    require(probe['start_ticks'] is None or _tick(probe['start_ticks'])!=record['child_start'])
    if record['state']=='complete':
        require(record['reason'] is None and type(record['result']) is dict and
                set(record['result'])=={'logical_regular_file_bytes',
                                        'regular_file_count','directory_count'})
        result=record['result'];size=result['logical_regular_file_bytes']
        require(type(size) is str and re.fullmatch(r'(?:0|[1-9][0-9]{0,18})',size) is not None and
                int(size)<=2**63-1 and all(type(result[key]) is int and
                0 <= result[key] <= 10_000 for key in ('regular_file_count','directory_count')))
    else:
        require(type(record['reason']) is str and record['reason'] in
                ('READ_FAILED','READ_TIMEOUT','CLEANUP_UNVERIFIED') and record['result'] is None)
    return {'record':record,'probe':probe}
