"""Commit-only Kustomize closure and inhibited inventory, never an apply manifest.

Unknown/external inputs are gaps, not permission to fetch. Only the deliberately
small supported Kustomize dialect is passed to the local, plugin-disabled renderer.
"""
import copy
import hashlib
import json
import posixpath
from pathlib import Path
import re
import shutil
import subprocess
import tempfile

import yaml

from tools.local_experiment import source_candidate as C

R = C.S.R
CONFIG_NAMES = ('kustomization.yaml', 'kustomization.yml', 'Kustomization')
FIELDS = {'apiVersion', 'kind', 'resources', 'namespace', 'patches', 'replacements',
          'configMapGenerator', 'generatorOptions'}
WORKLOADS = {'Deployment', 'StatefulSet', 'ReplicaSet', 'ReplicationController',
             'DaemonSet', 'Job', 'CronJob', 'Pod'}


def documents(raw):
    # Duplicate keys must not conceal a remote resource or executable generator.
    class Loader(yaml.SafeLoader):
        pass

    def mapping(loader, node):
        pairs = loader.construct_pairs(node, deep=True)
        R.require(len({k for k, _ in pairs}) == len(pairs), 'RENDER_DUPLICATE_KEY')
        return dict(pairs)

    Loader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, mapping)
    try:
        return [v for v in yaml.load_all(raw, Loader=Loader) if v is not None]
    except yaml.YAMLError:
        raise R.RunnerError('RENDER_YAML_INVALID') from None


class Closure:
    def __init__(self, root, commit):
        R.require(re.fullmatch(r'[0-9a-f]{40}', commit or '') is not None,
                  'CANDIDATE_FULL_COMMIT_REQUIRED')
        R.require(C.git(root, 'cat-file', '-t', commit) == 'commit', 'CANDIDATE_COMMIT_REQUIRED')
        self.root, self.commit = root, commit
        self.entries = {}
        for row in C.git(root, 'ls-tree', '-r', commit).splitlines():
            meta, name = row.split('\t', 1)
            mode, kind, oid = meta.split()
            self.entries[name] = (mode, kind, oid)
        self.files, self.gaps, self.active, self.visited = {}, [], set(), set()

    def gap(self, where, reason, value):
        record = {'path': where, 'reason': reason, 'input': value}
        if record not in self.gaps:
            self.gaps.append(record)

    def resolve(self, parent, value):
        if not isinstance(value, str) or not value or re.search(r'[:?@\\]', value) or value.startswith('/'):
            self.gap(parent, 'external_or_invalid_input', value)
            return None
        p = posixpath.normpath(posixpath.join(parent, value))
        if p == '..' or p.startswith('../'):
            self.gap(parent, 'repository_escape', value)
            return None
        return p

    def blob(self, p):
        entry = self.entries.get(p)
        if not entry or entry[0] not in ('100644', '100755') or entry[1] != 'blob':
            self.gap(p, 'unavailable_or_nonregular_commit_input', p)
            return None
        # cat-file through the same no-lazy-fetch/no-replacement environment.
        # C.git strips whitespace, so read the exact blob via its object ID instead.
        import os
        env = dict(os.environ, GIT_NO_LAZY_FETCH='1', GIT_NO_REPLACE_OBJECTS='1',
                   GIT_TERMINAL_PROMPT='0', GIT_CONFIG_NOSYSTEM='1')
        result = subprocess.run(['git', '--no-replace-objects', 'cat-file', 'blob', entry[2]],
                                cwd=self.root, env=env, capture_output=True, timeout=20)
        R.require(result.returncode == 0, 'CANDIDATE_LOCAL_OBJECT_UNAVAILABLE')
        raw = result.stdout
        R.require(len(raw) <= 8*1024*1024 and
                  hashlib.sha1(b'blob '+str(len(raw)).encode()+b'\0'+raw).hexdigest() == entry[2],
                  'RENDER_BLOB_INVALID')
        self.files[p] = raw
        return raw

    def directory(self, p):
        if p in self.active:
            self.gap(p, 'cyclic_kustomize_input', p)
            return
        if p in self.visited:
            return
        self.active.add(p)
        configs = [p+'/'+n for n in CONFIG_NAMES if p+'/'+n in self.entries]
        if len(configs) != 1:
            self.gap(p, 'missing_or_ambiguous_kustomization', p)
        else:
            raw = self.blob(configs[0])
            if raw is not None:
                docs = documents(raw)
                R.require(len(docs) == 1 and isinstance(docs[0], dict), 'RENDER_CONFIG_INVALID')
                config = docs[0]
                unknown = sorted(set(config)-FIELDS)
                if unknown:
                    self.gap(p, 'unsupported_kustomize_fields', unknown)
                for value in config.get('resources', []):
                    target = self.resolve(p, value)
                    if target is None:
                        continue
                    if target in self.entries:
                        self.blob(target)
                    else:
                        self.directory(target)
                for patch in config.get('patches', []):
                    if not isinstance(patch, dict) or set(patch)-{'path', 'patch', 'target', 'options'}:
                        self.gap(p, 'unsupported_patch', patch)
                    elif 'path' in patch:
                        target = self.resolve(p, patch['path'])
                        if target is not None:
                            self.blob(target)
                for replacement in config.get('replacements', []):
                    if not isinstance(replacement, dict) or set(replacement)-{'path', 'source', 'targets'}:
                        self.gap(p, 'unsupported_replacement', replacement)
                    elif 'path' in replacement:
                        target = self.resolve(p, replacement['path'])
                        if target is not None:
                            self.blob(target)
                for generator in config.get('configMapGenerator', []):
                    if set(generator)-{'name', 'namespace', 'files', 'literals', 'options', 'behavior'}:
                        self.gap(p, 'unsupported_generator', generator)
                    for value in generator.get('files', []):
                        target = self.resolve(p, value.split('=', 1)[-1])
                        if target is not None:
                            self.blob(target)
        self.active.remove(p)
        self.visited.add(p)

    def render(self, p, staging, binary):
        before = len(self.gaps)
        self.directory(p)
        # A previous incomplete shared closure must also inhibit rendering.
        if self.gaps or len(self.gaps) != before:
            return None
        with tempfile.TemporaryDirectory(dir=staging, prefix='commit-render-') as temp:
            base = Path(temp)
            for name, raw in self.files.items():
                dest = base/name
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(raw)
            result = subprocess.run([binary, 'kustomize', str(base/p)], capture_output=True,
                                    timeout=60)
            R.require(result.returncode == 0 and len(result.stdout) <= 8*1024*1024,
                      'RENDER_LOCAL_BUILD_FAILED')
            return documents(result.stdout)


def pod_spec(obj):
    kind, spec = obj['kind'], obj.get('spec', {})
    if kind == 'Pod':
        return spec
    if kind == 'CronJob':
        return spec.get('jobTemplate', {}).get('spec', {}).get('template', {}).get('spec', {})
    return spec.get('template', {}).get('spec', {})


def inventory(objects, candidate):
    workloads, images, references, gaps = [], [], [], []
    identities = set()
    for obj in objects:
        R.require(isinstance(obj, dict) and all(k in obj for k in ('apiVersion', 'kind', 'metadata')),
                  'RENDER_OBJECT_INVALID')
        identity = (obj['apiVersion'], obj['kind'], obj['metadata'].get('namespace'), obj['metadata']['name'])
        R.require(identity not in identities, 'RENDER_DUPLICATE_RESOURCE')
        identities.add(identity)
        owner = dict(zip(('apiVersion', 'kind', 'namespace', 'name'), identity))
        if obj['kind'] == 'GitRepository' and obj.get('spec', {}).get('secretRef', {}).get('name'):
            references.append(dict(owner, input_kind='Secret',
                input_name=obj['spec']['secretRef']['name'], scope='committed_transport_not_selected'))
        if obj['kind'] == 'HelmRelease':
            gaps.append(dict(owner, reason='external_chart_not_rendered'))
        if obj['kind'] not in WORKLOADS:
            # Do not silently omit an unknown custom workload containing images.
            if re.search(r'"(?:image|containers|initContainers)"\s*:', json.dumps(obj.get('spec', {}))):
                gaps.append(dict(owner, reason='unsupported_workload_surface'))
            continue
        spec = pod_spec(obj)
        R.require(isinstance(spec.get('containers'), list) and spec['containers'], 'RENDER_POD_SPEC_INVALID')
        projection = copy.deepcopy(obj)
        controller = next((v for v in candidate['controllers'] if v['name'] == owner['name'] and
                           owner['namespace'] == 'flux-system' and owner['kind'] == 'Deployment'), None)
        if controller:
            R.require([v['image'] for v in spec['containers']] == controller['images'], 'RENDER_CONTROLLER_PIN_DRIFT')
            projection['spec']['replicas'] = 1
        elif obj['kind'] in ('Job', 'CronJob'):
            projection['spec']['suspend'] = True
        elif obj['kind'] in ('Deployment', 'StatefulSet', 'ReplicaSet', 'ReplicationController'):
            projection['spec']['replicas'] = 0
        else:
            gaps.append(dict(owner, reason='workload_has_no_safe_inhibited_projection'))
        workloads.append(dict(owner, committed_sha256=C.digest(obj), inhibited_sha256=C.digest(projection),
                              inhibition='controller_one' if controller else
                              'unsupported' if obj['kind'] in ('DaemonSet', 'Pod') else 'inhibited'))
        for field in ('containers', 'initContainers', 'ephemeralContainers'):
            for container in spec.get(field, []):
                ref = container.get('image')
                R.require(isinstance(ref, str) and bool(ref), 'RENDER_IMAGE_INVALID')
                images.append(dict(owner, field=field, container=container['name'], reference=ref,
                    digest_pinned=bool(re.fullmatch(r'[^\s]+@sha256:[0-9a-f]{64}', ref)),
                    image_bytes_verified=False))

        def refs(value):
            if isinstance(value, dict):
                for key, child in value.items():
                    kind = {'secretKeyRef': 'Secret', 'secretRef': 'Secret', 'configMapKeyRef': 'ConfigMap',
                            'configMapRef': 'ConfigMap', 'configMap': 'ConfigMap', 'secret': 'Secret',
                            'persistentVolumeClaim': 'PersistentVolumeClaim'}.get(key)
                    if kind and isinstance(child, dict):
                        name = child.get('name') or child.get('secretName') or child.get('claimName')
                        if name:
                            references.append(dict(owner, input_kind=kind, input_name=name))
                    refs(child)
            elif isinstance(value, list):
                for child in value:
                    refs(child)
        refs(spec)
        for ref in spec.get('imagePullSecrets', []):
            references.append(dict(owner, input_kind='Secret', input_name=ref['name']))
        if spec.get('serviceAccountName'):
            references.append(dict(owner, input_kind='ServiceAccount', input_name=spec['serviceAccountName']))
    available = {(i['kind'], i['metadata'].get('namespace'), i['metadata']['name']) for i in objects}
    for ref in references:
        # StatefulSet claim templates are generated runtime objects, not repository PVCs.
        ref['repository_available'] = (ref['input_kind'], ref['namespace'], ref['input_name']) in available
    return workloads, images, references, gaps


def build(root, candidate, staging, binary='kubectl'):
    """Render local committed closure only; no runtime/API/fetch or risk approval."""
    commit = candidate['identity']['commit']
    closure = Closure(root, commit)
    R.require(candidate['identity']['tree'] == C.git(root, 'rev-parse', commit+'^{tree}') and
              candidate['identity']['root_path'] == './'+C.ROOT_PATH and
              candidate['source']['ref'] == {'commit': commit} and candidate['source']['suspend'] is True and
              candidate['source']['transport'] == 'unselected_local_only' and
              {v['name']: C.path(v['path']) for v in candidate['kustomizations']} ==
              dict(C.CHILDREN, **{'flux-system': C.ROOT_PATH}) and
              len(candidate['kustomizations']) == 7 and
              all(v['suspend'] is True for v in candidate['kustomizations']) and
              len(candidate['controllers']) == 4 and
              {v['name'] for v in candidate['controllers']} == C.S.FLUX and
              all(v['replicas'] == 1 for v in candidate['controllers']), 'RENDER_CANDIDATE_INVALID')
    executable = shutil.which(binary)
    R.require(executable is not None, 'RENDERER_UNAVAILABLE')
    renderer_hash = hashlib.sha256(Path(executable).read_bytes()).hexdigest()
    rendered, objects = [], []

    def render_path(p):
        # One blocked child must not hide inventories of independent local children.
        local = Closure(root, commit)
        docs = local.render(p, staging, executable)
        closure.files.update(local.files)
        for gap in local.gaps:
            closure.gap(gap['path'], gap['reason'], gap['input'])
        return docs

    root_objects = render_path(C.ROOT_PATH)
    if root_objects is not None:
        children = [o for o in root_objects if o['kind'] == 'Kustomization' and
                    o['apiVersion'].startswith('kustomize.toolkit.fluxcd.io/')]
        R.require({o['metadata']['name']: C.path(o['spec']['path']) for o in children} ==
                  dict(C.CHILDREN, **{'flux-system': C.ROOT_PATH}) and len(children) == 7,
                  'RENDER_FLUX_SCOPE_DRIFT')
        sources = [o for o in root_objects if o['kind'] == 'GitRepository']
        R.require(len(sources) == 1 and sources[0]['metadata']['name'] == 'flux-system', 'RENDER_FLUX_SCOPE_DRIFT')
        rendered.append({'path': C.ROOT_PATH, 'sha256': C.digest(root_objects), 'objects': len(root_objects)})
        objects.extend(root_objects)
        for reconciler in sorted(children, key=lambda o: o['metadata']['name']):
            spec = reconciler['spec']
            R.require(spec['sourceRef'] == {'kind': 'GitRepository', 'name': 'flux-system'}, 'RENDER_FLUX_SCOPE_DRIFT')
            p = C.path(spec['path'])
            unsupported = set(spec)-{'dependsOn', 'interval', 'retryInterval', 'timeout', 'path',
                                     'prune', 'sourceRef', 'postBuild', 'wait', 'healthChecks', 'suspend', 'force'}
            if unsupported:
                closure.gap(p, 'unsupported_flux_render_fields', sorted(unsupported))
                continue
            if p == C.ROOT_PATH:
                continue
            post = spec.get('postBuild', {})
            if set(post)-{'substitute'}:
                closure.gap(p, 'external_or_unsupported_postbuild', post)
                continue
            docs = render_path(p)
            if docs is None:
                continue
            substitutions = post.get('substitute', {})
            raw = json.dumps(docs)
            variables = set(re.findall(r'\$\{([^}]+)\}', raw))
            if (any(not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', v) or v not in substitutions for v in variables)
                    or re.search(r'\$\$|\$[A-Za-z_]', raw)):
                closure.gap(p, 'unavailable_or_unsupported_substitution', sorted(variables))
            else:
                # Replace inside scalar strings, never YAML/JSON syntax.
                def substitute(v):
                    if isinstance(v, str):
                        return re.sub(r'\$\{([A-Za-z_][A-Za-z0-9_]*)\}', lambda m: str(substitutions[m[1]]), v)
                    if isinstance(v, list):
                        return [substitute(i) for i in v]
                    if isinstance(v, dict):
                        return {k: substitute(i) for k, i in v.items()}
                    return v
                docs = substitute(docs)
            if any(o['kind'] in ('Kustomization', 'GitRepository', 'HelmRelease') for o in docs):
                closure.gap(p, 'unsupported_nested_flux_input', p)
            rendered.append({'path': p, 'sha256': C.digest(docs), 'objects': len(docs),
                             'substitutions': substitutions})
            objects.extend(docs)
    workloads, images, refs, gaps = inventory(objects, candidate)
    inputs = [{'path': p, 'blob': closure.entries[p][2], 'sha256': hashlib.sha256(raw).hexdigest()}
              for p, raw in sorted(closure.files.items())]
    resources = []
    workload_hashes = {(w['apiVersion'], w['kind'], w['namespace'], w['name']): w['inhibited_sha256']
                       for w in workloads}
    for obj in objects:
        projection = copy.deepcopy(obj)
        if obj['kind'] == 'GitRepository':
            # This projection is deliberately not a valid source manifest/transport.
            projection['spec'] = copy.deepcopy(candidate['source'])
        elif obj['kind'] == 'Kustomization' and obj['apiVersion'].startswith('kustomize.toolkit.fluxcd.io/'):
            projection['spec']['suspend'] = True
        key = (obj['apiVersion'], obj['kind'], obj['metadata'].get('namespace'), obj['metadata']['name'])
        resources.append({'apiVersion': obj['apiVersion'], 'kind': obj['kind'],
                          'namespace': obj['metadata'].get('namespace'), 'name': obj['metadata']['name'],
                          'committed_sha256': C.digest(obj),
                          'candidate_projection_sha256': workload_hashes.get(key, C.digest(projection))})
    return {'schema_version': 1, 'mode': 'repository_only_inhibited_render_inventory',
            'candidate_sha256': C.digest(candidate), 'identity': copy.deepcopy(candidate['identity']),
            'renderer': {'binary_sha256': renderer_hash, 'plugins_enabled': False, 'network_inputs_allowed': False},
            'inputs': inputs, 'renders': rendered, 'resources': resources, 'workloads': workloads, 'images': images,
            'runtime_input_references': refs, 'gaps': closure.gaps+gaps,
            'repository_inventory_complete': not closure.gaps and not gaps and len(rendered) == 7,
            'repository_references_complete': not any(not r['repository_available'] for r in refs),
            'runtime_inputs_complete': False,  # External image bytes/runtime inputs were not inspected.
            'local_risk_decision': {'status': 'missing', 'decision': None},
            'admission_verified': False, 'activation_authorized': False, 'gate_acceptance': False,
            'fetched_commit': None, 'applied_commit': None,
            'limitations': ['inventory is not an applyable inhibited manifest',
                            'committed source/root are unsafe to apply; candidate transport stays unselected',
                            'runtime dependencies and external image bytes are not fetched or verified',
                            'strict HIGH findings and missing local risk decision remain blocking']}


def validate(root, value, candidate, staging, binary='kubectl'):
    R.require(value == build(root, candidate, staging, binary), 'RENDER_INVENTORY_DRIFT')
    return True


def bind_admission(root, contract, candidate, items, baseline, staging, binary='kubectl'):
    """Revalidate strict findings and fresh candidate, then bind inventory, not approval."""
    from tools.local_experiment import admission_contract as A
    A.validate(root, contract, candidate, items, baseline)
    value = build(root, candidate, staging, binary)
    bound = copy.deepcopy(contract)
    bound['transitive_render_inventory'] = {
        'status': 'repository_complete_runtime_gaps' if value['repository_inventory_complete'] else 'incomplete',
        'inventory_sha256': C.digest(value), 'inventory': value}
    bound['limitations'][-1] = 'runtime input gaps and local risk decision still require separate review'
    return bound
