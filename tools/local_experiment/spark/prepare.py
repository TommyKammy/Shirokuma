"""Cache one official Spark image in the dedicated Colima Docker CRI runtime.

The current k3s node uses Docker CRI, not a standalone containerd image store.
This helper never creates a workload, changes a tag, exports/imports an image,
or deletes existing cache contents. Exact-reference CRI ImageStatus is the
preflight proof for imagePullPolicy Never; it is not a Pod execution claim.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import urllib.request
from urllib.parse import urlsplit
import uuid

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from tools.local_experiment.spark.contract import IMAGE, VERSION
from tools.local_experiment.trino_opa.common import (
    ExperimentError, command as shared_command, directory, interruption_handlers, require, write)

CONTEXT = 'colima-mac-studio-solo'
PROFILE = 'mac-studio-solo'
STATE = ROOT / 'tmp/local-experiment/spark-prepare'
DIGEST = 'sha256:82d7d3c2315a8b826d83485728013c01d1f2542708c83c0c641a363924d065b7'
CONFIG_DIGEST = 'sha256:b9d69759274ca76b6222666ba37a5dcd39f922e915a0d6a868bce3b4cfdc4a9d'
COMPRESSED_BYTES = 794127774
LAYERS = 11
MIN_FREE_BYTES = 4 * 1024**3
SOURCE = 'https://github.com/apache/spark-docker/blob/994aa3719a9d6f759d50d7d9d4ba5ad23f30cb7f/4.1.3/scala2.13-java17-python3-ubuntu/Dockerfile'
REGISTRY = 'https://registry-1.docker.io/v2/library/spark/manifests/' + DIGEST
SHA = re.compile(r'sha256:[0-9a-f]{64}')


def command(args, **kwargs):
    return shared_command(['rtk', 'proxy', *args], **kwargs)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ExperimentError('REGISTRY_REDIRECT_REJECTED')


def official_manifest():
    """Anonymous public pull token stays in memory; only pinned manifest is saved."""
    opener = urllib.request.build_opener(NoRedirect())
    def get(url, headers=None):
        with opener.open(urllib.request.Request(url, headers=headers or {}), timeout=30) as response:
            require(response.status == 200 and response.geturl() == url, 'REGISTRY_RESPONSE_INVALID')
            raw = response.read(1024**2 + 1)
            require(len(raw) <= 1024**2, 'REGISTRY_RESPONSE_LIMIT')
            return raw
    auth = json.loads(get('https://auth.docker.io/token?service=registry.docker.io&scope=repository:library/spark:pull'))
    token = auth.get('token')
    require(type(token) is str and 1 <= len(token) <= 16384 and '\r' not in token and '\n' not in token,
            'REGISTRY_TOKEN_INVALID')
    raw = get(REGISTRY, {'Authorization': 'Bearer ' + token,
                        'Accept': 'application/vnd.oci.image.manifest.v1+json,application/vnd.docker.distribution.manifest.v2+json'})
    require('sha256:' + hashlib.sha256(raw).hexdigest() == DIGEST, 'MANIFEST_DIGEST_MISMATCH')
    return validate_manifest(json.loads(raw))


def validate_manifest(value):
    require(type(value) is dict and value.get('schemaVersion') == 2, 'MANIFEST_INVALID')
    require(value.get('mediaType') in ('application/vnd.oci.image.manifest.v1+json',
                                     'application/vnd.docker.distribution.manifest.v2+json'), 'MANIFEST_TYPE_INVALID')
    config = value.get('config', {})
    require(config.get('digest') == CONFIG_DIGEST and type(config.get('size')) is int and 0 < config['size'] <= 1024**2,
            'CONFIG_DIGEST_MISMATCH')
    layers = value.get('layers')
    require(type(layers) is list and len(layers) == LAYERS, 'LAYER_COUNT_MISMATCH')
    safe = []
    for layer in layers:
        require(type(layer) is dict and type(layer.get('digest')) is str and SHA.fullmatch(layer['digest'])
                and type(layer.get('size')) is int and 0 < layer['size'] <= 1024**3, 'LAYER_INVALID')
        require(layer.get('mediaType') in ('application/vnd.oci.image.layer.v1.tar+gzip',
                                         'application/vnd.docker.image.rootfs.diff.tar.gzip'), 'LAYER_TYPE_INVALID')
        safe.append({'digest': layer['digest'], 'size': layer['size']})
    require(sum(item['size'] for item in safe) == COMPRESSED_BYTES, 'LAYER_BYTES_MISMATCH')
    return {'image': IMAGE, 'manifest_digest': DIGEST, 'config_digest': CONFIG_DIGEST,
            'compressed_layers_bytes': COMPRESSED_BYTES, 'layer_count': LAYERS,
            'layers': safe, 'registry': REGISTRY, 'dockerfile_source': SOURCE,
            'official_images_catalog': 'https://github.com/docker-library/official-images/blob/master/library/spark'}


def docker(*args):
    return ['docker', '--context', CONTEXT, *args]


def ssh(*args):
    return ['colima', '--profile', PROFILE, 'ssh', '--', *args]


def guard(run=command):
    require(IMAGE == 'docker.io/library/spark@' + DIGEST and VERSION == '4.1.3', 'CONTRACT_IMAGE_CHANGED')
    contexts = json.loads(run(docker('context', 'inspect', CONTEXT)))
    require(len(contexts) == 1 and contexts[0].get('Name') == CONTEXT
            and contexts[0].get('Endpoints', {}).get('docker', {}).get('Host')
            == 'unix://' + str(Path.home() / '.colima/mac-studio-solo/docker.sock'), 'DOCKER_CONTEXT_MISMATCH')
    profiles = [json.loads(line) for line in run(['colima', 'list', '--json']).splitlines() if line.strip()]
    selected = [p for p in profiles if p.get('name') == PROFILE]
    require(len(selected) == 1 and selected[0].get('status') == 'Running'
            and selected[0].get('arch') == 'aarch64' and selected[0].get('runtime') == 'docker+k3s', 'COLIMA_PROFILE_MISMATCH')
    base = ['kubectl', '--context', CONTEXT, '--request-timeout=15s']
    config = json.loads(run(base + ['config', 'view', '--minify', '-o', 'json']))
    clusters = config.get('clusters', [])
    require(len(clusters) == 1, 'KUBERNETES_CONTEXT_MISMATCH')
    server = urlsplit(clusters[0]['cluster']['server'])
    require(server.scheme == 'https' and server.hostname in {'localhost', '127.0.0.1', '::1'}
            and server.username is None and server.password is None, 'KUBERNETES_CONTEXT_MISMATCH')
    nodes = json.loads(run(base + ['get', 'nodes', '-o', 'json'])).get('items', [])
    require(len(nodes) == 1 and nodes[0]['metadata']['name'] == CONTEXT, 'KUBERNETES_NODE_MISMATCH')
    node = nodes[0]; info = node['status']['nodeInfo']
    require(info.get('architecture') == 'arm64' and info.get('operatingSystem') == 'linux'
            and info.get('kubeletVersion') == 'v1.33.4+k3s1'
            and info.get('containerRuntimeVersion', '').startswith('docker://'), 'NODE_RUNTIME_MISMATCH')
    daemon = json.loads(run(docker('info', '--format', '{{json .}}')))
    require(daemon.get('Architecture') == 'aarch64' and daemon.get('OSType') == 'linux'
            and daemon.get('DockerRootDir') == '/var/lib/docker' and daemon.get('ID')
            and ['driver-type', 'io.containerd.snapshotter.v1'] in daemon.get('DriverStatus', []), 'DOCKER_DAEMON_MISMATCH')
    return {'context': CONTEXT, 'profile': PROFILE, 'node_uid': node['metadata']['uid'],
            'node_name': CONTEXT, 'kubelet_version': info['kubeletVersion'],
            'container_runtime': info['containerRuntimeVersion'], 'docker_id': daemon['ID'],
            'docker_root': daemon['DockerRootDir'], 'image_store': 'containerd-snapshotter',
            'architecture': 'arm64', 'os': 'linux'}


def disk(run=command):
    lines = run(ssh('df', '-Pk', '/var/lib/docker')).splitlines()
    require(len(lines) == 2, 'DISK_OBSERVATION_INVALID')
    fields = lines[1].split()
    require(len(fields) == 6 and fields[-1] == '/var/lib/docker', 'DISK_OBSERVATION_INVALID')
    return {'filesystem': fields[0], 'total_bytes': int(fields[1]) * 1024,
            'used_bytes': int(fields[2]) * 1024, 'available_bytes': int(fields[3]) * 1024,
            'mount': fields[-1]}


def inventory(run=command):
    values = []
    for line in run(docker('image', 'ls', '--all', '--no-trunc', '--digests', '--format', '{{json .}}')).splitlines():
        if line.strip():
            row = json.loads(line)
            require(SHA.fullmatch(row['ID']), 'IMAGE_INVENTORY_INVALID')
            values.append({k: row[k] for k in ('ID', 'Repository', 'Tag', 'Digest')})
    require(len(values) <= 1024, 'IMAGE_INVENTORY_LIMIT')
    return sorted(values, key=lambda x: json.dumps(x, sort_keys=True))


def cache_content(run=command):
    """Read only the two immutable blobs from Docker's existing content store."""
    result = {}
    for kind, digest in (('manifest', DIGEST), ('config', CONFIG_DIGEST)):
        raw = run(ssh('sudo', 'k3s', 'ctr', '--address', '/run/containerd/containerd.sock',
                      '--namespace', 'moby', 'content', 'get', digest), output_limit=1024**2)
        require('sha256:' + hashlib.sha256(raw.encode()).hexdigest() == digest, 'CACHED_CONTENT_HASH_MISMATCH')
        result[kind] = json.loads(raw)
    validate_manifest(result['manifest'])
    return result


def validate_cache(inspected, cri, content):
    require(type(inspected) is list and len(inspected) == 1, 'IMAGE_INSPECT_INVALID')
    image = inspected[0]
    aliases = {IMAGE, 'spark@' + DIGEST, 'library/spark@' + DIGEST}
    require(image.get('Id') == DIGEST and image.get('Architecture') == 'arm64'
            and image.get('Os') == 'linux' and aliases.intersection(image.get('RepoDigests', []))
            and image.get('Descriptor', {}).get('digest') == DIGEST, 'CACHED_IMAGE_IDENTITY')
    config = image.get('Config', {})
    require(config.get('User') == 'spark' and config.get('Entrypoint') == ['/opt/entrypoint.sh']
            and config.get('WorkingDir') == '/opt/spark/work-dir'
            and 'SPARK_HOME=/opt/spark' in config.get('Env', []), 'CACHED_IMAGE_CONFIG')
    diff_ids = image.get('RootFS', {}).get('Layers', [])
    require(len(diff_ids) == LAYERS and all(type(d) is str and SHA.fullmatch(d) for d in diff_ids), 'CACHED_LAYER_COUNT')
    raw_config = content.get('config', {})
    # Docker adds explicit nulls for these absent OCI fields when inspecting.
    # No non-null value or arbitrary additional field is removed.
    inspected_config = {key: value for key, value in config.items()
                        if not (key in {'Cmd', 'OnBuild', 'Volumes'} and value is None
                                and key not in raw_config.get('config', {}))}
    require(raw_config.get('architecture') == 'arm64' and raw_config.get('os') == 'linux'
            and raw_config.get('rootfs', {}).get('diff_ids') == diff_ids
            and raw_config.get('config') == inspected_config, 'CACHED_CONFIG_CONTENT_MISMATCH')
    validate_manifest(content.get('manifest'))
    require(type(image.get('Size')) is int and 0 < image['Size'] < MIN_FREE_BYTES, 'CACHED_SIZE_INVALID')
    status = cri.get('status', {})
    require(status.get('id') == DIGEST and aliases.intersection(status.get('repoDigests', []))
            and str(status.get('size', '')).isdigit() and int(status['size']) > 0, 'CRI_IMAGE_STATUS_MISMATCH')
    return {'image': IMAGE, 'config_digest': CONFIG_DIGEST, 'manifest_digest': DIGEST,
            'architecture': 'arm64', 'os': 'linux', 'docker_reported_image_bytes': image['Size'],
            'cached_manifest_config_hashes_verified': True,
            'rootfs_diff_ids': diff_ids, 'cri_image_id': status['id'], 'cri_reported_bytes': int(status['size']),
            'exact_reference_resolved': True, 'image_pull_policy': 'Never', 'workload_started': False}


def prepare(output=None, *, run=command, registry=official_manifest):
    if output is None:
        directory(STATE, create=True)
        output = STATE / uuid.uuid4().hex
        directory(output, create=True)
    output = Path(output)
    directory(output)
    require(not (output / 'summary.json').exists(), 'OUTPUT_ALREADY_USED')
    receipt = {'schema_version': 1, 'result': 'failed', 'image': IMAGE, 'pending': None, 'workload_started': False,
               'started_at': datetime.now(timezone.utc).isoformat()}
    write(output / 'summary.json', receipt)
    try:
        before = guard(run)
        images_before = inventory(run)
        capacity = disk(run)
        require(capacity['available_bytes'] >= MIN_FREE_BYTES, 'INSUFFICIENT_IMAGE_HEADROOM')
        manifest = registry()
        require(manifest['image'] == IMAGE and manifest['manifest_digest'] == DIGEST
                and manifest['config_digest'] == CONFIG_DIGEST, 'REGISTRY_IDENTITY_MISMATCH')
        write(output / 'registry.json', manifest)
        write(output / 'before.json', {'runtime': before, 'images': images_before, 'disk': capacity})
        require(guard(run) == before, 'RUNTIME_CHANGED')
        cached = any(row['ID'] == DIGEST and row['Digest'] == DIGEST for row in images_before)
        if not cached:
            receipt['pending'] = {'operation': 'pull', 'image': IMAGE}
            write(output / 'summary.json', receipt)
            run(docker('pull', '--platform', 'linux/arm64', IMAGE), timeout=900, output_limit=8 * 1024**2,
                log_path=output / 'pull.json')
        cache = validate_cache(json.loads(run(docker('image', 'inspect', IMAGE))),
            json.loads(run(ssh('sudo', 'k3s', 'crictl', '--timeout', '10s', 'inspecti', '--output', 'json', IMAGE))),
            cache_content(run))
        require(guard(run) == before, 'RUNTIME_CHANGED')
        images_after = inventory(run)
        require(all(row in images_after for row in images_before), 'EXISTING_IMAGES_CHANGED')
        added = [row for row in images_after if row not in images_before]
        require(all(row['ID'] == DIGEST and row['Digest'] == DIGEST and row['Tag'] == '<none>'
                    and row['Repository'] in {'spark', 'library/spark', 'docker.io/library/spark'} for row in added),
                'UNEXPECTED_IMAGE_ADDED')
        after_disk = disk(run)
        write(output / 'after.json', {'runtime': before, 'images': images_after, 'disk': after_disk, 'cache': cache})
        receipt.update(result='passed', pending=None, observed_at=datetime.now(timezone.utc).isoformat(),
                       already_cached=cached, runtime=before, image_evidence=cache,
                       compressed_layers_bytes=COMPRESSED_BYTES, layer_count=LAYERS,
                       retained_image_rows=len(images_before), added_image_rows=len(added), existing_images_unchanged=True,
                       disk_before=capacity, disk_after=after_disk,
                       allocated_disk_delta_bytes=after_disk['used_bytes'] - capacity['used_bytes'],
                       scope='official digest direct pull into existing Docker CRI cache; no workload or container created')
    except BaseException as error:
        receipt['failure_code'] = str(error) if isinstance(error, ExperimentError) else type(error).__name__
        write(output / 'summary.json', receipt)
        raise
    write(output / 'summary.json', receipt)
    return receipt, output


def main():
    argparse.ArgumentParser(description=__doc__).parse_args()
    try:
        with interruption_handlers():
            value, output = prepare()
        print(json.dumps({'result': value['result'], 'image': IMAGE, 'evidence': str(output.relative_to(ROOT)),
                          'already_cached': value['already_cached'], 'workload_started': False}))
        return 0
    except BaseException:
        print(json.dumps({'result': 'failed', 'workload_started': False}))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
