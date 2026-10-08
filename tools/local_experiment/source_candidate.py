"""Offline inhibited source contract, NOT a Flux manifest or activation request.

Only existing local Git objects are read. Transport is deliberately unselected:
neither an external URL nor a local serving resource is authorized by this record.
Fresh runtime observations and admission/transport review remain separate gates.
"""
import hashlib
import json
import os
from pathlib import PurePosixPath
import re
import subprocess

from tools.local_experiment import gate_supervisor as S

ROOT_PATH = 'deploy/gitops/clusters/local-lite'
CHILDREN = {
    'shirokuma-catalog': 'deploy/gitops/catalog/server',
    'shirokuma-catalog-bootstrap': 'deploy/catalog/bootstrap',
    'shirokuma-catalog-database': 'deploy/gitops/catalog/database',
    'shirokuma-dev': 'deploy/gitops/dev',
    'shirokuma-iceberg-bootstrap': 'deploy/gitops/iceberg/bootstrap',
    'shirokuma-object-storage': 'deploy/gitops/object-storage',
}


def git(root, *args):
    env = dict(os.environ, GIT_NO_LAZY_FETCH='1', GIT_NO_REPLACE_OBJECTS='1',
               GIT_TERMINAL_PROMPT='0', GIT_CONFIG_NOSYSTEM='1')
    try:
        result = subprocess.run(['git', '--no-replace-objects', *args], cwd=root,
                                env=env, capture_output=True, timeout=20)
    except (OSError, subprocess.TimeoutExpired):
        raise S.R.RunnerError('CANDIDATE_LOCAL_OBJECT_UNAVAILABLE') from None
    S.R.require(result.returncode == 0, 'CANDIDATE_LOCAL_OBJECT_UNAVAILABLE')
    return result.stdout.decode().strip()


def path(value):
    S.R.require(isinstance(value, str), 'CANDIDATE_PATH_INVALID')
    value = value[2:] if value.startswith('./') else value
    S.R.require(bool(value) and not PurePosixPath(value).is_absolute() and
                all(p not in ('', '.', '..') for p in value.split('/')) and
                re.fullmatch(r'[A-Za-z0-9_./-]+', value) is not None,
                'CANDIDATE_PATH_INVALID')
    return value


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def build(root, commit, items, baseline):
    """Bind available commit/tree and inhibited observed identities; never mutate.

    The caller must retain the fresh complete probe separately. UID/spec hashes
    bind this snapshot, not future clearance to fetch/apply. No status is copied
    into candidate identity or promoted to fetched/applied evidence.
    """
    S.R.require(isinstance(commit, str) and re.fullmatch(r'[0-9a-f]{40}', commit),
                'CANDIDATE_FULL_COMMIT_REQUIRED')
    S.R.require(git(root, 'cat-file', '-t', commit) == 'commit',
                'CANDIDATE_COMMIT_REQUIRED')
    tree = git(root, 'rev-parse', '--verify', commit+'^{tree}')
    S.R.require(len({i['metadata']['uid'] for i in items}) == len(items),
                'CANDIDATE_DUPLICATE_IDENTITY')
    S.validate(items, baseline, S.READY)
    reconcilers = [i for i in items if i['kind'] in ('GitRepository', 'Kustomization')]
    expected = dict(CHILDREN, **{'flux-system': ROOT_PATH})
    source = [i for i in reconcilers if i['kind'] == 'GitRepository']
    S.R.require(len(source) == 1 and source[0]['metadata']['name'] == 'flux-system' and
                source[0]['metadata'].get('namespace') == 'flux-system', 'CANDIDATE_SOURCE_SCOPE')
    paths = []
    seen = set()
    for obj in reconcilers:
        if obj['kind'] != 'Kustomization':
            continue
        name = obj['metadata']['name']; spec = obj['spec']
        p = path(spec.get('path'))
        S.R.require(name in expected and name not in seen and p == expected[name] and
                    obj['metadata'].get('namespace') == 'flux-system' and
                    spec.get('sourceRef') == {'kind': 'GitRepository', 'name': 'flux-system'},
                    'CANDIDATE_RECONCILER_SCOPE')
        seen.add(name)
        oid = git(root, 'rev-parse', '--verify', commit+':'+p)
        S.R.require(git(root, 'cat-file', '-t', oid) == 'tree', 'CANDIDATE_PATH_NOT_TREE')
        # A path symlink/blob cannot qualify as a source root.
        paths.append({'name': name, 'path': './'+p, 'tree': oid, 'suspend': True})
    S.R.require(seen == set(expected), 'CANDIDATE_RECONCILER_SCOPE')
    controllers = []
    for obj in items:
        if obj['kind'] == 'Deployment' and obj['metadata'].get('namespace') == 'flux-system':
            images = [c['image'] for c in obj['spec']['template']['spec']['containers']]
            S.R.require(all(re.fullmatch(r'[^\s]+@sha256:[0-9a-f]{64}', i) for i in images),
                        'CANDIDATE_IMAGE_UNPINNED')
            controllers.append({'name': obj['metadata']['name'], 'replicas': 1, 'images': images})
    guard_kinds = {'GitRepository', 'Kustomization', 'Deployment', 'StatefulSet',
                   'ReplicaSet', 'Job', 'PersistentVolume', 'PersistentVolumeClaim'}
    guards = [{'kind': i['kind'], 'namespace': i['metadata'].get('namespace'),
               'name': i['metadata']['name'], 'uid': i['metadata']['uid'],
               'spec_sha256': digest(i['spec'])} for i in items if i['kind'] in guard_kinds]
    return {'schema_version': 1, 'mode': 'repository_only_inhibited_candidate',
            'identity': {'commit': commit, 'tree': tree, 'root_path': './'+ROOT_PATH},
            'source': {'name': 'flux-system', 'namespace': 'flux-system',
                       'ref': {'commit': commit}, 'suspend': True,
                       'transport': 'unselected_local_only'},
            'kustomizations': sorted(paths, key=lambda i: i['name']),
            'controllers': sorted(controllers, key=lambda i: i['name']),
            'observed_guards': sorted(guards, key=lambda i: (i['kind'], i['name'])),
            'inhibition': {'jobs_suspended': True, 'retained_app_owners_replicas': 0},
            'evidence': {'local_git_objects': True, 'fetched_commit': None,
                         'artifact_bytes_verified': False, 'applied_commit': None,
                         'transitive_render_verified': False, 'admission_verified': False,
                         'gate_acceptance': False},
            'permitted_operations': ['local_object_inspection', 'repository_tests'],
            'prohibited_operations': ['fetch', 'apply', 'redirect_source', 'unsuspend',
                                     'start_writer', 'create_runtime_resource', 'activate_policy_binding']}
