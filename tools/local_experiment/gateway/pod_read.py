"""Bound the fixed local Pod list, including its context and Node guards.

Only the local kubeconfig, one Node list and the first namespace Pod page are
read. All three stages share a 20 second deadline; an owned process may require
the transport's additional two second reap allowance. The existing 2 MiB JSON
limit applies to each API response (local config retains its smaller limit).
kubectl/client-go may retry internally; this is not an HTTP request-count claim.
Responses stay in memory. A continuation token is returned, never followed, so
the caller can mark its observation incomplete.
"""
from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import time


HERE = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location('gateway_pod_read_usage', HERE / 'pod_usage.py')
U = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(U)

NAMESPACE = 'shirokuma-dev'
READ_SECONDS = 20
MAX_BYTES = 2 * 1024 * 1024
NODES_PATH = '/api/v1/nodes'
PODS_PATH = f'/api/v1/namespaces/{NAMESPACE}/pods?limit=50'


def _list(value, kind):
    U.require(isinstance(value, dict) and value.get('apiVersion') == 'v1' and
              value.get('kind') == kind and isinstance(value.get('items'), list) and
              isinstance(value.get('metadata'), dict), 'READ_FAILED')
    U.require(isinstance(value['metadata'].get('continue', ''), str), 'READ_FAILED')
    return value['items']


def fixed_list():
    """Return the fixed PodList or propagate a bounded read/cleanup failure."""
    deadline = time.monotonic() + READ_SECONDS
    environment = {key: os.environ[key] for key in ('PATH', 'HOME', 'USER', 'TMPDIR', 'LANG', 'LC_ALL')
                   if key in os.environ}

    def remaining():
        U.require(time.monotonic() < deadline, 'READ_TIMEOUT')

    def read(path):
        remaining()
        value = U._command(['--server', server, '--request-timeout=6s', 'get', '--raw', path],
                           deadline=deadline, environment=environment, _max_bytes=MAX_BYTES)
        remaining()
        return value

    remaining()
    server = U._local_server(deadline=deadline, environment=environment)
    remaining()
    nodes = read(NODES_PATH)
    items = _list(nodes, 'NodeList')
    U.require(not nodes['metadata'].get('continue') and len(items) == 1, 'READ_FAILED')
    node = items[0]
    U.require(isinstance(node, dict) and isinstance(node.get('status'), dict), 'READ_FAILED')
    info = node['status'].get('nodeInfo')
    U.require(isinstance(info, dict) and info.get('architecture') == 'arm64' and
              isinstance(info.get('kubeletVersion'), str) and 'k3s' in info['kubeletVersion'], 'READ_FAILED')
    pods = read(PODS_PATH)
    items = _list(pods, 'PodList')
    U.require(len(items) <= 50, 'READ_FAILED')
    for pod in items:
        U.require(isinstance(pod, dict) and isinstance(pod.get('metadata'), dict), 'READ_FAILED')
        metadata = pod['metadata']
        U.require(metadata.get('namespace') == NAMESPACE and U.name(metadata.get('name')), 'READ_FAILED')
    remaining()
    return pods
