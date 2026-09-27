"""Correlate saved XS session claims, not a live cleanup or query probe."""
import hashlib
import json
import os
from pathlib import Path
import re
import stat

from tools.local_experiment.warehouse_lifecycle import session_rpc as RPC, status as S

MAX_RECEIPT = 256 * 1024
MAX_OPERATION = 4096


def _file(directory, name, device, limit):
    fd=os.open(name,S.FILE_FLAGS,dir_fd=directory)
    try:
        before=os.fstat(fd)
        S.require(stat.S_ISREG(before.st_mode) and before.st_uid==os.getuid() and
                  stat.S_IMODE(before.st_mode)==0o600 and before.st_nlink==1 and
                  before.st_dev==device and 0<before.st_size<=limit)
        raw=os.read(fd,limit+1)
        after=os.fstat(fd)
        current=os.stat(name,dir_fd=directory,follow_symlinks=False)
        S.require(len(raw)==before.st_size and before==after and
                  (before.st_dev,before.st_ino)==(current.st_dev,current.st_ino) and
                  stat.S_ISREG(current.st_mode))
        return raw
    finally:os.close(fd)


def _saved_files(root,token,expected_names):
    root=Path(root)
    for path in (root,*root.parents):S.require(stat.S_ISDIR(path.lstat().st_mode))
    base=os.open(root,S.DIRECTORY_FLAGS)
    try:
        device=os.fstat(base).st_dev
        S.require(os.fstat(base).st_uid==os.getuid() and stat.S_IMODE(os.fstat(base).st_mode)==0o700)
        run=S._dir(base,token,device)
        try:
            receipt=_file(run,'receipt.json',device,MAX_RECEIPT)
            directory=S._dir(run,'operations',device)
            try:
                S.require(set(os.listdir(directory))==expected_names)
                operations={name:_file(directory,name,device,MAX_OPERATION) for name in sorted(expected_names)}
                S.require(set(os.listdir(directory))==expected_names)
            finally:os.close(directory)
            return receipt,operations
        finally:os.close(run)
    finally:os.close(base)


def _json(raw):
    return json.loads(raw.decode('utf-8'),object_pairs_hook=S._pairs,
                      parse_constant=lambda _:S.require(False))


def correlate(raw_journal,raw_receipt,operations,token):
    """Reject conflicting saved claims; do not attest their common producer."""
    S.project(raw_journal,token)
    journal=_json(raw_journal);receipt=_json(raw_receipt)
    S.require(type(receipt) is dict and receipt.get('run_token')==token and
              receipt.get('result')=='passed' and receipt.get('pending') is None)
    session=receipt.get('session');cleanup=receipt.get('cleanup');telemetry=receipt.get('telemetry')
    S.require(type(session) is dict and session.get('phase')=='closed' and
              type(cleanup) is dict and cleanup.get('verified') is True and
              cleanup.get('conflicts')==[] and cleanup.get('deferred')==[] and
              type(cleanup.get('removed')) is list and len(cleanup['removed'])<=64 and
              type(telemetry) is dict and telemetry.get('result')=='passed' and
              telemetry.get('cleanup_verified') is True and telemetry.get('saved_spans_verified') is True)
    server=session.get('server_result');saved=session.get('requests');entries=journal['requests']
    S.require(type(server) is dict and server.get('result')=='verified' and
              server.get('error_codes')==journal['error_codes']==[] and
              type(server.get('completed')) is int and server['completed']==len(entries) and
              type(server.get('requests')) is int and server['requests']==len(journal['replies']) and
              type(saved) is list and len(saved)==len(entries) and len(entries)>0)
    query_mode=any(entry['request']['action']=='query' for entry in entries)
    S.require((session.get('query_mode') is True if query_mode else 'query_mode' not in session) and
              entries[-1]['request']['action']==('query' if query_mode else 'wake') and
              (not query_mode or sum(entry['request']['action']=='query' for entry in entries)==1))
    names=set()
    for entry,record in zip(entries,saved):
        req=entry['request'];response=entry['response']
        S.require(entry['state']=='completed' and entry['error_code'] is None and
                  type(record) is dict and response['result'] in ('verified','noop') and
                  record.get('request_id')==req['request_id'] and record.get('action')==req['action'] and
                  record.get('operation_id')==response['operation_id'] and
                  record.get('result')==response['result'] and
                  record.get('receipt_sha256')==response['receipt_sha256'])
        name=response['operation_id']+'.json'
        S.require(record.get('receipt')=='operations/'+name and name not in names)
        names.add(name)
        S.require(name in operations)
        operation=_json(operations[name])
        S.require(type(operation) is dict and operation.get('failure_code') is None and
                  type(operation.get('duration_ms')) is int and operation['duration_ms']>=0 and
                  all(operation.get(key)==response[key] for key in RPC.RESPONSE_KEYS-{'receipt_sha256'}) and
                  ('query' not in response or operation.get('query')==response['query']))
        # The owner hashed the on-disk operation file bytes, including whitespace.
        S.require(hashlib.sha256(operations[name]).hexdigest()==response['receipt_sha256'])
    S.require(set(operations)==names)
    return {'recorded_completion':'correlated-saved-claim','saved_receipt_sha256':hashlib.sha256(raw_receipt).hexdigest(),
            'operation_count':len(entries),'runtime_state':'unknown'}


def read(token,root=S.RUNS):
    try:
        before=S._read(Path(root),token)
        S.project(before,token)
        journal=_json(before)
        names=set()
        for entry in journal['requests']:
            response=entry['response']
            if entry['state']=='completed' and response and response['result'] in ('verified','noop'):
                names.add(response['operation_id']+'.json')
        S.require(0<len(names)<=16 and len(names)==len(journal['requests']))
        receipt,operations=_saved_files(root,token,names)
        result=correlate(before,receipt,operations,token)
        second_receipt,second_operations=_saved_files(root,token,names)
        S.require(before==S._read(Path(root),token) and receipt==second_receipt and
                  operations==second_operations)
        return {**result,'journal_sha256':hashlib.sha256(before).hexdigest()}
    except (OSError,ValueError,TypeError,KeyError,AttributeError,RecursionError,UnicodeError,RPC.RPCError):
        raise S.StatusError('SAVED_COMPLETION_UNVERIFIED') from None
