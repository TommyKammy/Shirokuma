"""Pure fixture contract for VM /proc visibility; no VM or volume I/O.

All inputs are caller-supplied. Passing this contract never proves the data
came from the intended VM, and ENOENT alone is not a remote cleanup receipt.
"""
from __future__ import annotations

import errno
import posixpath
import re
import uuid

MAX_MOUNTINFO = 1024*1024
MAX_STATUS = 4096
MAX_STAT = 4096
MAX_PID = 2**31-1
MAX_NUMBER = 2**63-1


class ProcfsError(ValueError):
    pass


def require(ok):
    if not ok:raise ProcfsError('PROCFS_UNVERIFIED')


def _positive(value, maximum=MAX_NUMBER):
    require(type(value) is int and 0<value<=maximum)
    return value


def _namespace(value,kind):
    require(type(value) is str and len(value)<=40)
    match=re.fullmatch(re.escape(kind)+r':\[([1-9][0-9]{0,18})\]',value)
    require(match is not None)
    result=int(match[1]);require(result<=MAX_NUMBER)
    return result


def _start(raw,pid):
    require(type(raw) is bytes and 0<len(raw)<=MAX_STAT and
            not any(separator in raw for separator in (b'\r',b'\v',b'\f',b'\x00')) and
            (b'\n' not in raw or (raw.endswith(b'\n') and raw.count(b'\n')==1)))
    # Linux /proc/PID/stat has a parenthesized comm with arbitrary spaces or
    # closing parentheses. The last ") " precedes the state and numeric fields.
    prefix=f'{pid} ('.encode()
    require(raw.startswith(prefix))
    tail=raw.rsplit(b') ',1)
    require(len(tail)==2 and tail[0].startswith(prefix))
    # Only the final LF is optional; the kernel separates numeric stat fields
    # with one ASCII space. split() alone accepts forged tabs and empty columns.
    columns=tail[1].removesuffix(b'\n')
    fields=columns.split(b' ')
    # Linux also reports lowercase tracing-stop/dead states; arbitrary letters
    # are not valid process states in a caller-supplied stat snapshot.
    require(len(fields)>=20 and fields[0] in
            (b'R', b'S', b'D', b'Z', b'T', b't', b'W', b'X', b'x', b'K', b'P', b'I'))
    require(all(fields))
    # Fields 4..22 in proc/PID/stat are decimal kernel counters/identifiers.
    # A forged nonnumeric field must not lend credibility to a start tick.
    for field in fields[1:20]:
        require(re.fullmatch(rb'(?:0|[1-9][0-9]{0,18}|-[1-9][0-9]{0,18})',field) is not None and
                abs(int(field))<=MAX_NUMBER)
    tick=fields[19]
    require(re.fullmatch(rb'(?:0|[1-9][0-9]{0,19})',tick) is not None and
            int(tick)<=MAX_NUMBER)
    # Later stat fields vary by kernel version; when supplied, they must still
    # be canonical decimal fields rather than unchecked text after starttime.
    for field in fields[20:]:
        require(re.fullmatch(rb'(?:0|[1-9][0-9]{0,19}|-[1-9][0-9]{0,19})',field) is not None and
                -(MAX_NUMBER+1)<=int(field)<=2**64-1)
    return tick.decode('ascii')


def _mount(raw,mount_id,device):
    require(type(raw) is bytes and 0<len(raw)<=MAX_MOUNTINFO and
            raw.endswith(b'\n') and
            not any(byte < 32 and byte != 10 or byte == 127 for byte in raw))
    try:
        text=raw.decode('utf-8')
        # Kernel mountinfo is LF-framed; Unicode separators are not records.
        require(all(char=='\n' or not char.isspace() or char in ' \t'
                    for char in text))
        lines=text[:-1].split('\n')
    except UnicodeError:raise ProcfsError('PROCFS_UNVERIFIED') from None
    matches=[];seen=set()
    for line in lines:
        # Kernel mountinfo uses one ASCII space between fields. Splitting on
        # generic whitespace conceals empty columns and raw tab separators.
        require('\t' not in line and all(line.split(' ')))
        halves=line.split(' - ')
        require(len(halves)==2)
        left,right=halves[0].split(' '),halves[1].split(' ')
        require(len(left)>=6 and len(right)==3 and
                re.fullmatch(r'[1-9][0-9]{0,18}',left[0]) is not None and
                re.fullmatch(r'(?:0|[1-9][0-9]{0,18})',left[1]) is not None and
                int(left[1])<=MAX_NUMBER and
                re.fullmatch(r'(?:0|[1-9][0-9]{0,18}):(?:0|[1-9][0-9]{0,18})',left[2]) is not None and
                all(int(part)<=MAX_NUMBER for part in left[2].split(':')))
        # A noncanonical mountpoint could disguise an overmount below /proc.
        # Decode the four mountinfo path escapes before lexical comparison.
        def canonical_path(encoded):
            require(re.fullmatch(r'(?:[^\\]|\\(?:040|011|012|134))*',encoded) is not None)
            path=re.sub(r'\\(040|011|012|134)',
                        lambda match: chr(int(match[1],8)),encoded)
            require(path.startswith('/') and not path.startswith('//') and
                    posixpath.normpath(path)==path)
            return path
        canonical_path(left[3])
        path=canonical_path(left[4])
        ident=int(left[0]);require(ident<=MAX_NUMBER and ident not in seen);seen.add(ident)
        # A malformed unrelated row cannot be silently ignored while treating
        # this caller-supplied snapshot as a complete mount inventory.
        for option_field in (left[5], right[2]):
            tokens=option_field.split(',')
            require(tokens[0] in ('rw','ro') and all(tokens) and
                    len(tokens)==len(set(tokens)) and
                    not any(token in ('rw','ro') for token in tokens[1:]))
        # The columns between mount options and " - " are propagation tags,
        # not arbitrary evidence. Refuse malformed IDs and contradictory tags.
        tags=set()
        for tag in left[6:]:
            if tag=='unbindable':
                kind=tag
            else:
                match=re.fullmatch(r'(shared|master|propagate_from):([1-9][0-9]{0,18})',tag)
                require(match is not None and int(match[2])<=MAX_NUMBER)
                kind=match[1]
            require(kind not in tags)
            tags.add(kind)
        # An unbindable mount cannot also carry peer/master propagation IDs.
        require('unbindable' not in tags or len(tags)==1)
        if path=='/proc':matches.append((ident,left,right))
        # A nested mount replacing procfs lookup of any PID is ambiguous.
        require(not re.fullmatch(r'/proc/(?:[0-9]+|self|thread-self)(?:/.*)?',path))
    require(len(matches)==1 and matches[0][0]==mount_id)
    _,left,right=matches[0]
    # Linux mountinfo prints the major:minor pair of the same st_dev encoded
    # in the caller's /proc stat. Decode Linux dev_t explicitly: this pure
    # fixture contract also runs on macOS, whose os.major/minor differ.
    major=((device>>8)&0xfff)|((device>>32)&~0xfff)
    minor=(device&0xff)|((device>>12)&~0xff)
    require(left[2]==f'{major}:{minor}')
    mount_options=left[5].split(',')
    require(mount_options[0] in ('rw','ro') and
            len(mount_options)==len(set(mount_options)) and
            not any(option in ('rw','ro') for option in mount_options[1:]))
    # Superblock options must describe a single, unambiguous access/visibility
    # mode; membership checks alone admit contradictory or repeated tokens.
    options=right[2].split(',')
    require(options[0] in ('rw','ro') and len(options)==len(set(options)) and
            not any(option in ('rw','ro') for option in options[1:]))
    require(left[3]=='/' and right[0]=='proc' and right[1]=='proc' and
            all(option in ('rw','ro','nosuid','nodev','noexec','relatime','hidepid=0','gid=0')
                for option in options) and
            not any(option.startswith('hidepid=') and option!='hidepid=0'
                     for option in mount_options+options))


def _lf_records(raw, maximum):
    require(type(raw) is bytes and 0<len(raw)<=maximum and raw.endswith(b'\n') and
            not any(byte < 32 and byte not in (9, 10) or byte == 127 for byte in raw))
    return raw


def project(*, boot_id, proc_device, proc_inode, proc_mount_id, mountinfo,
            fdinfo, self_pid, self_stat, init_stat, self_status,
            pid_namespace_link, init_pid_namespace_link, mount_namespace_link,
            target_pid, target_stat, target_errno):
    """Project structural VM/probe candidates from bounded fixture inputs."""
    require(type(boot_id) is str)
    try:parsed=uuid.UUID(boot_id)
    except ValueError:raise ProcfsError('PROCFS_UNVERIFIED') from None
    require(parsed.version==4 and str(parsed)==boot_id)
    for value in (proc_device,proc_inode,proc_mount_id):_positive(value)
    _positive(self_pid,MAX_PID);_positive(target_pid,MAX_PID)
    require(self_pid!=target_pid and target_pid!=1)
    namespace=_namespace(pid_namespace_link,'pid')
    require(namespace==_namespace(init_pid_namespace_link,'pid'))
    mount_namespace=_namespace(mount_namespace_link,'mnt')
    _start(self_stat,self_pid);_start(init_stat,1)
    status=_lf_records(self_status,MAX_STATUS)
    info=_lf_records(fdinfo,MAX_STATUS)
    require(len(re.findall(rb'^NSpid:',status,re.M))==1 and
            len(re.findall(rb'^mnt_id:',info,re.M))==1)
    match=re.findall(rb'^NSpid:[ \t]+([0-9]+)$',status,re.M)
    require(len(match)==1 and match[0]==str(self_pid).encode())
    matches=re.findall(rb'^mnt_id:[ \t]*([0-9]+)$',info,re.M)
    require(len(matches)==1 and matches[0]==str(proc_mount_id).encode())
    _mount(mountinfo,proc_mount_id,proc_device)
    if target_stat is None:
        require(type(target_errno) is int and target_errno==errno.ENOENT)
        start=None
    else:
        require(target_errno is None)
        start=_start(target_stat,target_pid)
    vm={'boot_id':boot_id,'pid_namespace':namespace,'mount_namespace':mount_namespace,
        'proc_device':proc_device,'proc_inode':proc_inode,'proc_mount_id':proc_mount_id}
    probe={'pid':target_pid,'start_ticks':start,'pid_namespace':namespace,
           'proc_mount_id':proc_mount_id,'query_complete':True}
    return {'vm':vm,'probe':probe}
