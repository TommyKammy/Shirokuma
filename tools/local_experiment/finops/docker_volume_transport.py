"""Private bounded Docker metadata transport; never mounts or scans a volume."""
from __future__ import annotations

import json
import math
from pathlib import Path
import re
import time

from tools.local_experiment.agenthouse import manage as AH
from tools.local_experiment.finops import docker_volume_preflight as V


class TransportError(ValueError):
    pass


def require(ok):
    if not ok:
        raise TransportError('SCOPE_INVALID')


def _json(raw):
    require(type(raw) is str)
    try:
        require(len(raw.encode('utf-8')) <= 2 * 1024 * 1024)
    except UnicodeEncodeError:
        raise TransportError('SCOPE_INVALID') from None
    def unique(pairs):
        value={}
        for key,item in pairs:
            require(key not in value);value[key]=item
        return value
    def finite_float(text):
        require(len(text) <= 128)
        number=float(text)
        mantissa=re.split('[eE]',text,maxsplit=1)[0]
        require(math.isfinite(number) and not (number == 0 and re.search('[1-9]',mantissa)))
        return number
    def bounded_int(text):
        require(len(text.lstrip('-')) <= 128)
        return int(text)
    try:
        value=json.loads(raw, object_pairs_hook=unique, parse_float=finite_float,
                         parse_int=bounded_int,
                         parse_constant=lambda _: (_ for _ in ()).throw(TransportError('SCOPE_INVALID')))
        stack=[(value,0)]
        while stack:
            item,depth=stack.pop()
            require(depth<=64)
            if type(item) is dict:
                for key,child in item.items():
                    key.encode('utf-8')
                    stack.append((child,depth+1))
            elif type(item) is list:
                stack.extend((child,depth+1) for child in item)
            elif type(item) is str:
                item.encode('utf-8')
        return value
    except (ValueError,TypeError,UnicodeError,RecursionError):
        raise TransportError('SCOPE_INVALID') from None


def _one(raw):
    value=_json(raw)
    require(type(value) is list and len(value) == 1 and type(value[0]) is dict)
    return value[0]


def _container(value):
    # Docker inspect may contain credentials in Config.Env. Whitelist before
    # keeping an inventory or passing data to the offline guard.
    require(type(value) is dict)
    config=value.get('Config');host=value.get('HostConfig');state=value.get('State')
    mounts=value.get('Mounts');labels=config.get('Labels') if type(config) is dict else None
    if labels is None:labels={}
    require(type(config) is dict and type(host) is dict and type(state) is dict and
            type(mounts) is list and len(mounts) <= 16 and
            all(type(entry) is dict for entry in mounts) and type(labels) is dict and
            type(host.get('RestartPolicy')) is dict)
    return {'Id':value.get('Id'),'Name':value.get('Name'),'Image':value.get('Image'),
            'State':{key:state.get(key) for key in
                     ('Status','Running','Paused','Restarting','Dead','StartedAt','FinishedAt')},
            'RestartCount':value.get('RestartCount'),
            'Mounts':[{key:entry.get(key) for key in
                       ('Type','Name','Driver','Source','Destination','RW')}
                      for entry in mounts],
            'Config':{'Labels':{AH.LABEL:labels.get(AH.LABEL)},'Image':config.get('Image')},
            'HostConfig':{'RestartPolicy':{
                'Name':host['RestartPolicy'].get('Name'),
                'MaximumRetryCount':host['RestartPolicy'].get('MaximumRetryCount')}}}


def collect(backend, *, monotonic=time.monotonic):
    """backend._command uses bounded fixed-argv transport and suppresses stderr."""
    deadline=monotonic()+20
    def read(argv):
        remaining=deadline-monotonic()
        require(remaining > 0)
        raw=backend._command(argv,timeout=remaining)
        require(monotonic() < deadline)
        return raw
    started=backend.now()
    context=_one(read(['docker','context','inspect',AH.CONTEXT]))
    endpoints=context.get('Endpoints')
    require(type(endpoints) is dict and type(endpoints.get('docker')) is dict)
    context={'Name':context.get('Name'), 'Endpoints':{'docker':{
        'Host':endpoints['docker'].get('Host')}}}
    endpoint=context['Endpoints']['docker']['Host']
    # Refuse a foreign context before reading any daemon/container details.
    # Pin the checked Unix endpoint, not a mutable context name, afterward.
    require(context['Name']==AH.CONTEXT and
            endpoint=='unix://'+str(Path.home()/'.colima/mac-studio-solo/docker.sock'))
    docker=['docker','--host',endpoint]
    info=read([*docker,'info','--format',
               '{{json .ID}}|{{json .OSType}}|{{json .Architecture}}|{{json .DockerRootDir}}'])
    require(type(info) is str)
    # Docker's formatted info can include one final LF, but never surrounding
    # padding, CRLF or additional lines. Do not normalize a malformed reply.
    body=info[:-1] if info.endswith('\n') else info
    require(body and body == body.strip() and '\n' not in body and '\r' not in body)
    fields=body.split('|')
    require(len(fields)==4)
    daemon=dict(zip(('ID','OSType','Architecture','DockerRootDir'),
                    (_json(field) for field in fields)))
    require(type(daemon['ID']) is str and 1 <= len(daemon['ID']) <= 128 and
            daemon['OSType']=='linux' and daemon['Architecture'] in ('aarch64','arm64') and
            daemon['DockerRootDir']==V.ROOT)
    volume=_one(read([*docker,'volume','inspect',AH.VOLUME]))
    volume={key:volume.get(key) for key in ('Name','Labels','Driver','Scope','Options','Mountpoint','CreatedAt')}
    labels=volume['Labels']
    require(type(labels) is dict and volume['Options'] in (None,{}) and
            volume['Name']==AH.VOLUME and volume['Driver']=='local' and
            volume['Scope']=='local' and volume['Mountpoint']==V.VOLUME_ROOT and
            labels.get(AH.LABEL)=='agenthouse-local')
    volume['Labels']={AH.LABEL:labels.get(AH.LABEL)}
    ids_text=read([*docker,'container','ls','-a','--no-trunc',
                   '--format','{{.ID}}'])
    require(type(ids_text) is str)
    try:
        require(len(ids_text.encode('ascii')) <= 16384)
    except UnicodeEncodeError:
        raise TransportError('SCOPE_INVALID') from None
    # Docker's --format emits one LF-terminated ID per row. Python splitlines()
    # also accepts CR and Unicode separators that the producer did not emit.
    require(ids_text.endswith('\n') and '\r' not in ids_text)
    ids=ids_text[:-1].split('\n')
    require(1 <= len(ids) <= V.MAX_CONTAINERS and len(set(ids)) == len(ids) and
            all(type(item) is str and len(item) == 64 and
                all(ch in '0123456789abcdef' for ch in item) for item in ids))
    containers=[]
    for identifier in ids:
        containers.append(_container(_one(read([
            *docker,'container','inspect',identifier]))))
    require({item['Id'] for item in containers} == set(ids))
    again=read([*docker,'container','ls','-a','--no-trunc','--format','{{.ID}}'])
    require(again==ids_text)
    require(read([*docker,'info','--format',
                  '{{json .ID}}|{{json .OSType}}|{{json .Architecture}}|{{json .DockerRootDir}}'])==info)
    target=[item for item in containers if item.get('Name') == '/'+AH.NAME]
    require(len(target)==1)
    boot_reply=read(['colima','ssh','--profile','mac-studio-solo','--',
                     'cat','/proc/sys/kernel/random/boot_id'])
    # A procfs cat emits exactly one UUID plus LF. Do not collapse terminal
    # controls or extra lines into a plausible identity with strip().
    require(type(boot_reply) is str and re.fullmatch(
        r'[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}\n', boot_reply) is not None)
    boot=boot_reply[:-1]
    return {'context':context,'daemon':daemon,'container':target[0],
            'volume':volume,'listed_ids':ids,'containers':containers,'boot_id':boot,
            'started_at':started,'observed_at':backend.now(),'listing_complete':True}
