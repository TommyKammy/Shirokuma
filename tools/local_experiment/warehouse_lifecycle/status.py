#!/usr/bin/env python3
"""Read one archived warehouse session RPC journal; never contact its owner."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path: sys.path.insert(0, str(ROOT))
from tools.local_experiment.warehouse_lifecycle import session_rpc as RPC

RUNS = ROOT / 'tmp/local-experiment/warehouse-lifecycle/runs'
MAX_JOURNAL_BYTES = 65536
DIRECTORY_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
FILE_FLAGS = os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC


class StatusError(ValueError):
    pass


def require(ok):
    if not ok: raise StatusError('JOURNAL_UNVERIFIED')


def _dir(fd, name, device):
    child=os.open(name,DIRECTORY_FLAGS,dir_fd=fd)
    s=os.fstat(child)
    if not (stat.S_ISDIR(s.st_mode) and stat.S_IMODE(s.st_mode)==0o700 and
            s.st_uid==os.getuid() and s.st_dev==device):
        os.close(child);raise StatusError('JOURNAL_UNVERIFIED')
    return child


def _current_root(root, fd):
    # A path returned to the caller must still name the directory we opened.
    for path in (root, *root.parents):
        current=path.lstat()
        require(stat.S_ISDIR(current.st_mode))
        if path==root:
            pinned=os.fstat(fd)
            require((current.st_dev,current.st_ino)==(pinned.st_dev,pinned.st_ino))


def _open_root(root):
    root=Path(root)
    require(root.is_absolute() and '..' not in root.parts)
    # O_NOFOLLOW on the final component alone does not protect an ancestor
    # replaced between lstat and open. Walk from / with a pinned parent FD.
    fd=os.open('/',DIRECTORY_FLAGS)
    try:
        for component in root.parts[1:]:
            child=os.open(component,DIRECTORY_FLAGS,dir_fd=fd)
            os.close(fd);fd=child
        state=os.fstat(fd)
        require(state.st_uid==os.getuid() and stat.S_IMODE(state.st_mode)==0o700)
        _current_root(root,fd)
        return fd
    except BaseException:
        os.close(fd)
        raise


def _read(root, token):
    require(type(token) is str and re.fullmatch('[0-9a-f]{32}',token) is not None)
    root=Path(root)
    base=_open_root(root)
    try:
        s=os.fstat(base)
        child=_dir(base,token,s.st_dev)
        try:
            journal=_dir(child,'rpc',s.st_dev)
            try:
                fd=os.open('journal.json',FILE_FLAGS,dir_fd=journal)
                try:
                    before=os.fstat(fd)
                    require(stat.S_ISREG(before.st_mode) and before.st_uid==os.getuid() and
                            stat.S_IMODE(before.st_mode)==0o600 and before.st_nlink==1 and
                            before.st_dev==s.st_dev and 0<before.st_size<=MAX_JOURNAL_BYTES)
                    raw=os.read(fd,MAX_JOURNAL_BYTES+1)
                    after=os.fstat(fd)
                    current=os.stat('journal.json',dir_fd=journal,follow_symlinks=False)
                    require(len(raw)==before.st_size and before==after and
                             (before.st_dev,before.st_ino)==(current.st_dev,current.st_ino) and
                             stat.S_ISREG(current.st_mode))
                    _current_root(root,base)
                    return raw
                finally: os.close(fd)
            finally: os.close(journal)
        finally: os.close(child)
    finally: os.close(base)


def _pairs(items):
    result={}
    for key,value in items:
        require(key not in result);result[key]=value
    return result


def project(raw,token,request_id=None):
    require(type(raw) is bytes and 0<len(raw)<=MAX_JOURNAL_BYTES and
            type(token) is str and re.fullmatch('[0-9a-f]{32}',token) is not None)
    require(request_id is None or RPC.canonical_uuid(request_id))
    try:
        value=json.loads(raw.decode('utf-8'),object_pairs_hook=_pairs,
                         parse_constant=lambda _:require(False))
        require(type(value) is dict and set(value)=={'schema_version','session_id','requests','replies','error_codes'})
        require(type(value['schema_version']) is int and value['schema_version']==1 and value['session_id']==token)
        require(type(value['requests']) is list and len(value['requests'])<=16 and
                type(value['replies']) is list and len(value['replies'])<=16 and
                type(value['error_codes']) is list and len(value['error_codes'])<=16)
        require(all(type(code) is str and re.fullmatch('[A-Z_]{1,64}',code) for code in value['error_codes']))
        seen={};actions=[];match=None
        for entry in value['requests']:
            require(type(entry) is dict and set(entry)=={'request','state','response','error_code'})
            request=RPC.parse_request(json.dumps(entry['request']).encode(),token,allow_query=True)
            require(request['request_id'] not in seen);seen[request['request_id']]=entry
            state=entry['state'];response=entry['response'];error=entry['error_code']
            require(state in ('pending','completed','rejected','unknown') and
                    (error is None or type(error) is str and re.fullmatch('[A-Z_]{1,64}',error)))
            if state=='pending':require(response is None and error is None);result='pending'
            else:
                RPC.validate_response(response,request)
                result=response['result']
                require(state!='rejected' or result=='unknown')
                require(state!='unknown' or result in ('unknown','failed'))
                require(error is None or result not in ('verified','noop'))
            actions.append({'action':request['action'],'recorded_result':result})
            if request['request_id']==request_id:
                match={'request_id':request_id,'observation':'recorded-in-snapshot',
                       'recorded_action':request['action'],'recorded_result':result,
                       'operation_id':response['operation_id'] if response is not None else None,
                       'receipt_sha256':response['receipt_sha256'] if response is not None else None}
        for reply in value['replies']:
            require(type(reply) is dict)
            request={key:reply.get(key) for key in RPC.REQUEST_KEYS}
            request=RPC.parse_request(json.dumps(request).encode(),token,allow_query=True)
            RPC.validate_response(reply,request)
            original=seen.get(request['request_id']);require(original is not None)
            # A reply is either the exact saved outcome for the original
            # request, or the owner-generated evidence-free uncertainty for
            # an in-flight request / same-ID conflicting action. In particular
            # a conflicting action cannot carry an operation or receipt hash.
            saved=(original['request']==request and original['response']==reply and
                   original['state']!='pending')
            interim=(reply==RPC._uncertain(request) and reply['result']=='unknown')
            require(saved or interim)
        output={'schema_version':1,'session_id':token,'journal_sha256':hashlib.sha256(raw).hexdigest(),
                'observation':'saved-journal-only','runtime_state':'unknown',
                'recorded_actions':actions,'reply_count':len(value['replies']),
                'recorded_error_count':len(value['error_codes'])}
        if request_id is not None:
            output['request_lookup']=match or {'request_id':request_id,'observation':'not-recorded-in-snapshot',
                    'recorded_action':None,'recorded_result':None,'operation_id':None,'receipt_sha256':None}
        return output
    except (ValueError,TypeError,KeyError,AttributeError,RecursionError,UnicodeError,RPC.RPCError):
        raise StatusError('JOURNAL_UNVERIFIED') from None


def read(token,root=RUNS,request_id=None):
    try:
        require(request_id is None or RPC.canonical_uuid(request_id))
        return project(_read(Path(root),token),token,request_id)
    except (OSError,ValueError,TypeError):raise StatusError('JOURNAL_UNVERIFIED') from None


def classify_recovery(token,root=RUNS,request_id=None):
    """Classify saved evidence only; never authorize replay or infer backend state."""
    require(RPC.canonical_uuid(request_id))
    result=read(token,root,request_id)
    lookup=result['request_lookup']
    result['recovery_classification']='unknown-no-replay'
    if lookup['observation']=='recorded-in-snapshot' and lookup['recorded_result'] in ('verified','noop'):
        from tools.local_experiment.warehouse_lifecycle import status_completion as completion
        try:
            claim=completion.read(token,root)
        except StatusError:
            pass  # An absent, incomplete or contradictory receipt cannot justify replay.
        else:
            require(result['journal_sha256']==claim['journal_sha256'])
            result['recovery_classification']='correlated-saved-completion'
    # Even an unknown classification must not describe a journal replaced while
    # optional completion files were inspected.
    require(read(token,root,request_id)=={key:value for key,value in result.items()
                                          if key!='recovery_classification'})
    result['runtime_state']='unknown'
    return result


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('session_id',nargs='?')
    parser.add_argument('--with-saved-completion',action='store_true')
    parser.add_argument('--classify-recovery',action='store_true')
    parser.add_argument('--request-id')
    parser.add_argument('--request-id-from-env',action='store_true')
    args=parser.parse_args(argv)
    # Make exports command-line variables as environment, avoiding token text
    # interpolation into its shell recipe. Direct CLI calls can pass an argument.
    token=args.session_id if args.session_id is not None else os.environ.get('WAREHOUSE_LIFECYCLE_RUN_TOKEN')
    request_id=(os.environ.get('WAREHOUSE_LIFECYCLE_REQUEST_ID') if args.request_id_from_env
                else args.request_id)
    try:
        if args.classify_recovery and (args.with_saved_completion or not RPC.canonical_uuid(request_id)):
            raise StatusError('JOURNAL_UNVERIFIED')
        if args.request_id_from_env and (args.request_id is not None or not RPC.canonical_uuid(request_id)):
            raise StatusError('JOURNAL_UNVERIFIED')
        result=(classify_recovery(token,RUNS,request_id) if args.classify_recovery else
                read(token,RUNS,request_id))
        if args.with_saved_completion:
            from tools.local_experiment.warehouse_lifecycle import status_completion as completion
            claim=completion.read(token,RUNS)
            if result['journal_sha256']!=claim['journal_sha256']:
                raise StatusError('SAVED_COMPLETION_UNVERIFIED')
            result.update(claim)
    except ValueError as error:
        print(str(error) if str(error) in ('JOURNAL_UNVERIFIED','SAVED_COMPLETION_UNVERIFIED')
              else 'SAVED_COMPLETION_UNVERIFIED',file=sys.stderr)
        return 1
    print(json.dumps(result,sort_keys=True,separators=(',',':')))
    return 0


if __name__=='__main__':raise SystemExit(main())
