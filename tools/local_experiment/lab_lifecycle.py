#!/usr/bin/env python3
"""One-command lifecycle for the dedicated local experiment lab."""
import argparse
import json
from pathlib import Path
import subprocess
import sys
from functools import wraps
import warehouse_resident as warehouse

ROOT = Path(__file__).resolve().parents[2]
PROFILE = 'mac-studio-solo'
CONTEXT = 'colima-' + PROFILE
BASELINE = './scripts/colima_baseline.sh'
FLUX = ('source-controller', 'kustomize-controller', 'helm-controller',
        'notification-controller')
CHILDREN = ('shirokuma-dev', 'shirokuma-object-storage', 'shirokuma-catalog-database',
            'shirokuma-catalog-bootstrap', 'shirokuma-catalog', 'shirokuma-iceberg-bootstrap',
            'shirokuma-trino')
JOBS = ('iceberg-table-bootstrap', 'iceberg-finance-table-bootstrap')
WORKLOADS = (('shirokuma-storage', 'statefulset/seaweedfs'),
             ('shirokuma-dev', 'statefulset/polaris-postgresql'),
             ('shirokuma-dev', 'deployment/polaris'),
             ('shirokuma-dev', 'deployment/polaris-finance'),
             ('shirokuma-dev', 'deployment/trino-local'))

# Kine stores the protobuf Node value as a BLOB. Equal-length replacement keeps
# its string lengths intact; SQLite handles the database and WAL transaction.
REPAIR_NODE_IP = r'''
import re, sqlite3, subprocess
ip = subprocess.check_output(['ip', '-4', '-o', 'addr', 'show', 'dev', 'eth0']).split()[3].split(b'/')[0]
if not re.fullmatch(rb'192\.168\.5\.[0-9]', ip):
    raise SystemExit('eth0 is not an 11-byte lab IP; refusing repair')
db = '/var/lib/rancher/k3s/server/db/state.db'
key = '/registry/minions/colima-mac-studio-solo'
def node(conn):
    row = conn.execute('SELECT id, value FROM kine WHERE name=? AND deleted=0 ORDER BY id DESC LIMIT 1', (key,)).fetchone()
    if row is None:
        raise SystemExit('lab Node record missing')
    old = set(re.findall(rb'(?<![0-9])192\.168\.5\.[0-9]{1,3}(?![0-9])', row[1]))
    if len(old) != 1 or len(next(iter(old))) != 11 or ip in old:
        raise SystemExit('no unambiguous stale 11-byte lab Node IP')
    return row, old.pop()
with sqlite3.connect('file:' + db + '?mode=rw', uri=True) as conn:
    node(conn)
subprocess.run(['service', 'k3s', 'stop'], check=True)
try:
    with sqlite3.connect('file:' + db + '?mode=rw', uri=True) as conn:
        (ident, value), old = node(conn)
        conn.execute('UPDATE kine SET value=? WHERE id=?', (value.replace(old, ip), ident))
finally:
    subprocess.run(['service', 'k3s', 'restart'], check=True)
'''


def run(*args, check=True, input=None):
    result = subprocess.run(args, cwd=ROOT, input=input, text=True, capture_output=True)
    if check and result.returncode:
        raise RuntimeError(f"{' '.join(args[:5])} failed: {result.stderr.strip() or result.stdout.strip()}")
    return result


def kubectl(namespace, *args):
    return run('kubectl', '--context', CONTEXT, '-n', namespace, *args)


def manager(component, command, **kwargs):
    return run('python3', f'tools/local_experiment/{component}/manage.py', command, **kwargs)


def status():
    summary = {'profile': 'local-experiment', 'ready': False}
    probes = {
        'lakehouse': ('go', 'run', './cmd/shirokuma', 'doctor', '--profile', 'local-experiment', '--output', 'json'),
        'agenthouse': ('python3', 'tools/local_experiment/agenthouse/manage.py', 'status'),
        'loki': ('python3', 'tools/local_experiment/loki/manage.py', 'status'),
    }
    for name, command in probes.items():
        try:
            result = run(*command, check=False)
            data = json.loads(result.stdout)
            healthy = isinstance(data, dict) and (
                data.get('status') == 'healthy' if name == 'lakehouse' else
                data.get('result') == 'ready' if name == 'agenthouse' else data.get('ready') is True)
            summary[name] = 'ready' if result.returncode == 0 and healthy else 'unhealthy'
        except (OSError, ValueError):
            summary[name] = 'unavailable'
    summary['ready'] = all(summary[name] == 'ready' for name in probes)
    print(json.dumps(summary))
    return 0 if summary['ready'] else 1


def _mutation(function):
    @wraps(function)
    def guarded(*args, **kwargs):
        with warehouse.lifecycle_mutation():
            return function(*args, **kwargs)
    return guarded


@_mutation
def up():
    if run(BASELINE, 'status', check=False).returncode:
        if run(BASELINE, 'start', check=False).returncode:
            run('colima', 'ssh', '--profile', PROFILE, '--', 'sudo', 'python3', '-c', REPAIR_NODE_IP)
        run(BASELINE, 'status')
    for name in FLUX:
        kubectl('flux-system', 'scale', 'deployment/' + name, '--replicas=1')
    for name in FLUX:
        kubectl('flux-system', 'rollout', 'status', 'deployment/' + name, '--timeout=180s')
    for namespace, resource in WORKLOADS:
        if resource == 'deployment/trino-local':
            # Consumer Jobs need BOTH catalog services, not merely PostgreSQL.
            for name in JOBS:
                kubectl('shirokuma-dev', 'patch', 'job/' + name, '--type=merge', '-p', '{"spec":{"suspend":false}}')
        kubectl(namespace, 'scale', resource, '--replicas=1')
        kubectl(namespace, 'rollout', 'status', resource, '--timeout=300s')
        if resource == 'statefulset/polaris-postgresql':
            kubectl('shirokuma-dev', 'patch', 'job/polaris-bootstrap', '--type=merge', '-p', '{"spec":{"suspend":false}}')
            kubectl('shirokuma-dev', 'wait', '--for=condition=complete', 'job/polaris-bootstrap', '--timeout=300s')
    for resource in ('bucket/shirokuma-local-source', *('kustomization/' + name for name in CHILDREN)):
        kubectl('flux-system', 'patch', resource, '--type=merge', '-p', '{"spec":{"suspend":null}}')
    manager('agenthouse', 'up')
    manager('loki', 'up')
    return status()


@_mutation
def down():
    failed = False
    for command in (('python3', 'tools/local_experiment/agenthouse/manage.py', 'stop'),
                    ('python3', 'tools/local_experiment/loki/manage.py', 'stop'),
                    ('colima', 'stop', '--profile', PROFILE)):
        try:
            result = run(*command, check=False)
            if result.returncode:
                print(result.stderr or result.stdout, file=sys.stderr)
                failed = True
        except OSError as error:
            print(error, file=sys.stderr)
            failed = True
    return int(failed)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('up', 'down', 'status'))
    args = parser.parse_args(argv)
    try:
        return {'up': up, 'down': down, 'status': status}[args.command]()
    except (OSError, RuntimeError) as error:
        print(f'lab: {error}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
