"""Private Darwin host-clock evidence for an offline receipt gate.

No VM or volume I/O. Mach absolute time excludes suspend; continuous time
includes it. A bounded clock comparison is not proof that no brief sleep
occurred, and a caller-supplied receipt is not authenticated by this module.
"""
from __future__ import annotations

import ctypes
import platform
import re
import uuid

MAX_WINDOW_NS = 30_000_000_000
MAX_PROBE_AGE_NS = 10_000_000_000
MAX_CLOCK_SKEW_NS = 50_000_000
MAX_SAMPLE_SPAN_NS = 10_000_000
MAX_TICK_NS = 2**63-1


class ClockError(ValueError):
    pass


def require(value):
    if not value:raise ClockError('HOST_CLOCK_UNVERIFIED')


def _boot(libc):
    value=ctypes.create_string_buffer(64)
    size=ctypes.c_size_t(len(value))
    query=libc.sysctlbyname
    query.argtypes=(ctypes.c_char_p,ctypes.c_void_p,ctypes.POINTER(ctypes.c_size_t),
                    ctypes.c_void_p,ctypes.c_size_t)
    query.restype=ctypes.c_int
    require(query(b'kern.bootsessionuuid',value,ctypes.byref(size),None,0)==0 and
            0<size.value<=len(value))
    try:
        raw=value.raw[:size.value].rstrip(b'\0').decode('ascii').lower()
        parsed=uuid.UUID(raw)
    except (UnicodeError,ValueError):
        raise ClockError('HOST_CLOCK_UNVERIFIED') from None
    require(parsed.version==4 and str(parsed)==raw)
    return raw


class _Timebase(ctypes.Structure):
    _fields_=[('numer',ctypes.c_uint32),('denom',ctypes.c_uint32)]


def sample():
    """Read one bounded local-only clock sample; Darwin only."""
    require(platform.system()=='Darwin')
    try:
        libc=ctypes.CDLL(None)
        base=_Timebase()
        libc.mach_timebase_info.argtypes=(ctypes.POINTER(_Timebase),)
        libc.mach_timebase_info.restype=ctypes.c_int
        require(libc.mach_timebase_info(ctypes.byref(base))==0 and
                0<base.numer<=10**9 and 0<base.denom<=10**9)
        absolute=libc.mach_absolute_time
        continuous=libc.mach_continuous_time
        absolute.argtypes=continuous.argtypes=()
        absolute.restype=continuous.restype=ctypes.c_uint64
        c0=continuous();a0=absolute()
        before=_boot(libc)
        after=_boot(libc)
        a1=absolute();c1=continuous()
        require(before==after and a0<=a1 and c0<=c1)
        convert=lambda ticks:ticks*base.numer//base.denom
        awake0,awake,awake_span=convert(a0),convert(a1),convert(a1-a0)
        included,continuous_span=convert(c1),convert(c1-c0)
        require(0<=awake0<=awake<=MAX_TICK_NS and 0<=included<=MAX_TICK_NS and
                awake_span<=MAX_SAMPLE_SPAN_NS and
                continuous_span<=MAX_SAMPLE_SPAN_NS and
                abs(continuous_span-awake_span)<=MAX_CLOCK_SKEW_NS)
        return {'boot_id':before,'awake_ns':awake,'continuous_ns':included}
    except (AttributeError,OSError,OverflowError):
        raise ClockError('HOST_CLOCK_UNVERIFIED') from None


def validate(start, probe, now):
    """Reject reboot, reset, stale observation and substantial host suspend."""
    for value in (start,probe,now):
        require(type(value) is dict and set(value)=={'boot_id','awake_ns','continuous_ns'})
        boot=value['boot_id']
        require(type(boot) is str and re.fullmatch(r'[0-9a-f-]{36}',boot) is not None)
        try:parsed=uuid.UUID(boot)
        except ValueError:raise ClockError('HOST_CLOCK_UNVERIFIED') from None
        require(parsed.version==4 and str(parsed)==boot)
        for key in ('awake_ns','continuous_ns'):
            require(type(value[key]) is int and 0<=value[key]<=MAX_TICK_NS)
    require(start['boot_id']==probe['boot_id']==now['boot_id'])
    for earlier,later,limit in ((start,probe,MAX_WINDOW_NS),
                                (probe,now,MAX_PROBE_AGE_NS),
                                (start,now,MAX_WINDOW_NS)):
        awake=later['awake_ns']-earlier['awake_ns']
        elapsed=later['continuous_ns']-earlier['continuous_ns']
        require(0<=awake<=limit and 0<=elapsed<=limit and
                abs(elapsed-awake)<=MAX_CLOCK_SKEW_NS)
    return {'host_boot_id':start['boot_id'],
            'start_monotonic':start['awake_ns']/1_000_000_000,
            'probe_monotonic':probe['awake_ns']/1_000_000_000,
            'now_monotonic':now['awake_ns']/1_000_000_000}
