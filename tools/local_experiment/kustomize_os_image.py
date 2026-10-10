"""Nonexecuting, OS-only local archive; upstream Go binary stays unchanged."""
from __future__ import annotations

import copy
import gzip
import io
import json
import os
from pathlib import Path
import re
import tarfile
import zlib

from tools.local_experiment import kustomize_os_layer as L

LIMIT = 192 * 1024**2
BINARY = 'usr/local/bin/kustomize-controller'
TAG = 'shirokuma-local/kustomize-controller:v1.9.6-pcre2-only-experimental'


def controller(blob, descriptor, diff_id):
    """Static exact COPY-layer validation; no extraction or binary invocation."""
    L.bound(blob, descriptor['digest'], descriptor['size'], 64*1024**2)
    decoder=zlib.decompressobj(31)
    plain=decoder.decompress(blob,LIMIT+1)
    L.require(len(plain)<=LIMIT and decoder.eof and not decoder.unused_data,'CONTROLLER_GZIP_BOUND')
    L.bound(plain,diff_id,len(plain),LIMIT)
    offset=0;ended=False;pending=False
    while offset<len(plain):
        header=plain[offset:offset+512]
        if header==bytes(512):
            L.require(not pending and len(plain)-offset>=1024 and not any(plain[offset:]),'CONTROLLER_TAR_END')
            ended=True;break
        item=tarfile.TarInfo.frombuf(header,'utf-8','strict')
        L.require(item.type in (tarfile.REGTYPE,tarfile.AREGTYPE,tarfile.DIRTYPE,tarfile.XHDTYPE),'CONTROLLER_TAR_TYPE')
        end=offset+512+item.size;padded=offset+512+((item.size+511)//512)*512
        L.require(0<=item.size<=LIMIT and padded<=len(plain) and not any(plain[end:padded]),'CONTROLLER_TAR_BOUND')
        if item.type==tarfile.XHDTYPE:
            L.require(not pending and item.size<=65536,'CONTROLLER_PAX_BOUND');pending=True
            body=plain[offset+512:end];seen=set()
            while body:
                size,sep,_=body.partition(b' ')
                L.require(sep and size.isdigit() and len(size)<=6,'CONTROLLER_PAX')
                length=int(size)
                L.require(len(size)+3<length<=len(body) and body[length-1]==10,'CONTROLLER_PAX')
                key,sep,value=body[len(size)+1:length-1].partition(b'=')
                L.require(sep and key in (b'mtime',b'ctime',b'atime') and key not in seen and
                          re.fullmatch(rb'-?\d+(\.\d+)?',value),'CONTROLLER_PAX')
                seen.add(key);body=body[length:]
        else:
            L.canonical(item.name);pending=False
            L.require(item.isreg() or item.size==0,'CONTROLLER_NONFILE_DATA')
        offset=padded
    L.require(ended,'CONTROLLER_TAR_INCOMPLETE')
    names=set();binary=None
    with tarfile.open(fileobj=io.BytesIO(plain),mode='r:') as archive:
        for member in archive:
            name=L.canonical(member.name)
            L.require(name not in names and member.uid==member.gid==0 and member.mode==0o755 and
                      set(member.pax_headers)<= {'mtime','ctime','atime'},'CONTROLLER_MEMBER')
            names.add(name)
            if member.isdir():L.require(name in ('usr','usr/local','usr/local/bin'),'CONTROLLER_EXTRA_PATH')
            else:
                L.require(member.isreg() and name==BINARY,'CONTROLLER_EXTRA_PATH')
                binary=archive.extractfile(member).read(LIMIT+1)
                L.require(len(binary)==member.size and binary[:6]==b'\x7fELF\x02\x01' and
                          binary[18:20]==b'\xb7\x00','CONTROLLER_ELF')
    L.require(binary is not None,'CONTROLLER_MISSING')
    return {'binary_sha256':L.apk.digest(binary),'binary_bytes':len(binary),
            'layer_sha256':L.apk.digest(blob),'diff_id':diff_id,'executed':False,'go_binary_patched':False}


def metadata(config, patch):
    settings=copy.deepcopy(json.loads(config))
    L.require(settings.get('architecture')=='arm64' and settings.get('os')=='linux' and
              settings['config'].get('Entrypoint')==['kustomize-controller'] and
              settings['config'].get('User')=='65534:65534','LOCAL_IMAGE_CONFIG')
    settings['rootfs']['diff_ids'].append('sha256:'+L.apk.digest(patch))
    settings.setdefault('history',[]).append({'created_by':'Shirokuma offline pcre2-only patch; Go binary unchanged'})
    settings['config'].setdefault('Labels',{}).update({
        'org.opencontainers.image.version':'v1.9.6-shirokuma-pcre2-only',
        'org.opencontainers.image.description':'Unsigned local OS-only experiment; not upstream release; Go binary unchanged',
        'io.shiro.local.publisher':'unverified-local-archive',
        'io.shiro.local.go-binary-patched':'false',
        'io.shiro.local.pcre2-layer-sha256':L.apk.digest(patch)})
    return json.dumps(settings,sort_keys=True,separators=(',',':')).encode()


def assemble(destination: Path, manifest, config, blobs, patch, provenance,
             index, package, parent, parent_sha256, scan):
    """All four layers preserved/bound; local file only, no load/import/run."""
    L.require(len(blobs)==3,'COMPLETE_LAYER_COUNT')
    L.validate_candidate_patch(patch,provenance,manifest,config,blobs[:2],index,
                               package,parent,parent_sha256,scan)
    original=json.loads(manifest);settings=json.loads(config)
    binary=controller(blobs[2],original['layers'][2],settings['rootfs']['diff_ids'][2])
    encoded=metadata(config,patch);config_name=L.apk.digest(encoded)+'.json'
    layers=blobs+[gzip.compress(patch,mtime=0)]
    names=[L.apk.digest(data)+'.tar.gz' for data in layers]
    L.require(len(set(names))==4,'DUPLICATE_LAYERS')
    saved=json.dumps([{'Config':config_name,'RepoTags':[TAG],'Layers':names}],sort_keys=True,separators=(',',':')).encode()
    L.require(sum(map(len,layers))+len(encoded)+len(saved)<128*1024**2,'LOCAL_ARCHIVE_BOUND')
    directory=L.apk.common.private_directory(destination.parent)
    try:fd=os.open(destination.name,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600,dir_fd=directory)
    finally:os.close(directory)
    with os.fdopen(fd,'wb') as stream,tarfile.open(fileobj=stream,mode='w',format=tarfile.USTAR_FORMAT) as archive:
        for name,data in list(zip(names,layers))+[(config_name,encoded),('manifest.json',saved)]:
            item=tarfile.TarInfo(name);item.mode=0o600;item.size=len(data)
            archive.addfile(item,io.BytesIO(data))
    return {'schema_version':1,'candidate_child':L.CHILD,'config_sha256':L.apk.digest(encoded),
            'tag':TAG,'controller':binary,'pcre2_version':'10.49-r0',
            'os_prefix_patch_sha256':L.apk.digest(patch),'original_layer_sha256':[L.apk.digest(b) for b in blobs],
            'archive_bytes':destination.stat().st_size,'full_layer_closure_verified':True,
            'image_executed':False,'go_binary_patched':False,'image_scan_complete':False,
            'installed_highs_mitigated':False,'publisher_verified':False,'admission':False,'l0':False}
