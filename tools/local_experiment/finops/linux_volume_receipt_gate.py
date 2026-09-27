"""Private host-side acceptance of a *verified* remote cleanup observation.

This pure function does not fetch or authenticate VM records. A future bounded
transport must supply an independently read terminal record and process probe
from the same pinned VM/boot; calling this with invented data proves nothing.
"""
from __future__ import annotations

import math
import re
import uuid


class ReceiptError(ValueError):
    pass


def require(ok, reason='CLEANUP_UNVERIFIED'):
    if not ok:
        raise ReceiptError(reason)


def _tick(value):
    require(type(value) is str and re.fullmatch(r'(?:0|[1-9][0-9]{0,19})',value) is not None)
    return int(value)


def accept(record, probe, *, operation_id, worker_digest, boot_id,
           host_boot_id, operation_started_monotonic, now_monotonic):
    """Return logical-file counts only when terminal and process proof agree."""
    try:parsed=uuid.UUID(operation_id)
    except (ValueError,TypeError,AttributeError):
        raise ReceiptError('CLEANUP_UNVERIFIED') from None
    require(parsed.version==4 and str(parsed)==operation_id and
            type(worker_digest) is str and re.fullmatch(r'[a-f0-9]{64}',worker_digest) is not None and
            type(boot_id) is str and len(boot_id)==36 and
            type(host_boot_id) is str and len(host_boot_id)==36 and
            type(record) is dict and type(probe) is dict)
    require(set(record)=={'operation_id','worker_digest','boot_id','state','reason',
                          'child_pid','child_start','cleanup_verified','result'} and
            set(probe)=={'boot_id','host_boot_id','pid','start_ticks',
                         'observed_monotonic','query_complete'} and
            record['operation_id']==operation_id and record['worker_digest']==worker_digest and
            record['boot_id']==probe['boot_id']==boot_id and
            probe['host_boot_id']==host_boot_id and
            type(record['child_pid']) is int and record['child_pid']>0 and
            probe['pid']==record['child_pid'] and type(probe['pid']) is int and
            probe['query_complete'] is True and record['cleanup_verified'] is True)
    start=_tick(record['child_start'])
    for value in (operation_started_monotonic,probe['observed_monotonic'],now_monotonic):
        require(type(value) in (float,int) and math.isfinite(value) and value>=0)
    require(0 <= probe['observed_monotonic']-operation_started_monotonic <= 30 and
            0 <= now_monotonic-probe['observed_monotonic'] <= 10 and
            now_monotonic-operation_started_monotonic <= 30)
    # `None` means the exact /proc PID was absent, not that the probe failed.
    # A reused PID has a different start tick; the original worker is gone.
    current_start=probe['start_ticks']
    require(current_start is None or _tick(current_start)!=start)
    require(record['state'] in ('complete','unknown') and
            (record['state']=='complete') == (record['reason'] is None))
    if record['state']!='complete':
        require(record['result'] is None)
        raise ReceiptError('READ_FAILED')
    result=record['result']
    require(type(result) is dict and set(result)=={
        'logical_regular_file_bytes','regular_file_count','directory_count'})
    size=result['logical_regular_file_bytes']
    require(type(size) is str and re.fullmatch(r'(?:0|[1-9][0-9]{0,18})',size) is not None and
            str(int(size))==size and int(size)<=2**63-1 and
            all(type(result[field]) is int and 0 <= result[field] <= 10_000
                for field in ('regular_file_count','directory_count')))
    return dict(result)


def accept_with_clock(record, probe, *, operation_id, worker_digest, boot_id,
                      start_sample, probe_sample, now_sample):
    """Offline candidate requiring matching Darwin clock observations.

    Samples must be taken by the trusted host sampler; injected structures do
    not prove provenance, and this function never contacts a VM.
    """
    from tools.local_experiment.finops import host_clock as H
    try:window=H.validate(start_sample,probe_sample,now_sample)
    except H.ClockError:
        raise ReceiptError('CLEANUP_UNVERIFIED') from None
    require(type(probe) is dict and type(probe.get('observed_monotonic')) in (int,float) and
            math.isfinite(probe['observed_monotonic']) and
            abs(probe['observed_monotonic']-window['probe_monotonic'])<=0.001)
    return accept(record,probe,operation_id=operation_id,worker_digest=worker_digest,
                  boot_id=boot_id,host_boot_id=window['host_boot_id'],
                  operation_started_monotonic=window['start_monotonic'],
                  now_monotonic=window['now_monotonic'])
