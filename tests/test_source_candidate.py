import copy
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock

from tools.local_experiment import source_candidate as C


def observations():
    def obj(kind, name, ns=None, **spec):
        return {'kind': kind, 'metadata': {'uid': kind+name, 'name': name,
                'namespace': ns, 'generation': 1}, 'spec': spec, 'status': {}}
    node = obj('Node', 'colima-mac-studio-solo')
    stamp = datetime.now(timezone.utc).isoformat()
    node['status']['conditions'] = [{'type': 'Ready', 'status': 'True', 'lastHeartbeatTime': stamp}]
    items = [node]
    lease = obj('Lease', node['metadata']['name'], 'kube-node-lease',
                holderIdentity=node['metadata']['name'], leaseDurationSeconds=40, renewTime=stamp)
    lease['metadata']['ownerReferences'] = [{'kind': 'Node', 'uid': node['metadata']['uid']}]
    items.append(lease)
    for name in sorted(C.S.FLUX):
        containers = [{'image': 'controller@sha256:'+'a'*64}]
        deploy = obj('Deployment', name, 'flux-system', replicas=1,
                     template={'spec': {'containers': containers}})
        deploy['status'] = {'observedGeneration': 1, 'availableReplicas': 1, 'readyReplicas': 1}
        rs = obj('ReplicaSet', name+'-rs', 'flux-system', replicas=1)
        rs['metadata']['ownerReferences'] = [{'uid': deploy['metadata']['uid'], 'controller': True}]
        pod = obj('Pod', name+'-pod', 'flux-system', containers=containers)
        pod['metadata']['ownerReferences'] = [{'uid': rs['metadata']['uid']}]
        pod['status']['conditions'] = [{'type': 'Ready', 'status': 'True'}]
        items += [deploy, rs, pod]
    items.append(obj('Deployment', 'app', 'shirokuma-dev', replicas=0))
    items.append(obj('GitRepository', 'flux-system', 'flux-system', suspend=True,
                     url='ssh://external.invalid/repo', ref={'branch': 'main'}))
    for name, p in dict(C.CHILDREN, **{'flux-system': C.ROOT_PATH}).items():
        items.append(obj('Kustomization', name, 'flux-system', suspend=True, path='./'+p,
                         prune=True, sourceRef={'kind': 'GitRepository', 'name': 'flux-system'}))
    items += [obj('Job', 'job'+str(n), 'shirokuma-dev', suspend=True) for n in range(3)]
    items += [obj('PersistentVolume', 'pv', claimRef={'uid': 'claim'}),
              obj('PersistentVolumeClaim', 'claim', 'shirokuma-dev', volumeName='pv')]
    return items


class SourceCandidateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        subprocess.run(['git', 'init', '-q', str(self.root)], check=True)
        for p in [C.ROOT_PATH, *C.CHILDREN.values()]:
            d = self.root/p; d.mkdir(parents=True, exist_ok=True)
            (d/'kustomization.yaml').write_text('resources: []\n')
        subprocess.run(['git', 'add', '.'], cwd=self.root, check=True)
        subprocess.run(['git', '-c', 'user.name=Fixture', '-c', 'user.email=fixture@invalid',
                        'commit', '-qm', 'fixture'], cwd=self.root, check=True)
        self.commit = C.git(self.root, 'rev-parse', 'HEAD')
        self.items = observations(); self.baseline = copy.deepcopy(self.items)

    def build(self, commit=None):
        return C.build(self.root, commit or self.commit, self.items, self.baseline)

    def test_available_full_commit_tree_paths_and_inhibition_bound_without_status_claims(self):
        original = copy.deepcopy(self.items)
        candidate = self.build()
        self.assertEqual(self.items, original)
        self.assertEqual(candidate['identity']['tree'], C.git(self.root, 'rev-parse', 'HEAD^{tree}'))
        self.assertEqual(candidate['source']['ref'], {'commit': self.commit})
        self.assertNotIn('url', candidate['source'])
        self.assertEqual(len(candidate['kustomizations']), 7)
        self.assertTrue(all(k['suspend'] for k in candidate['kustomizations']))
        self.assertEqual(len(candidate['controllers']), 4)
        self.assertTrue(all(k['replicas'] == 1 for k in candidate['controllers']))
        self.assertIsNone(candidate['evidence']['fetched_commit'])
        self.assertIsNone(candidate['evidence']['applied_commit'])
        self.assertFalse(candidate['evidence']['gate_acceptance'])
        self.assertNotIn('external.invalid', json.dumps(candidate))
        self.assertTrue(any(g['kind'] == 'PersistentVolumeClaim' for g in candidate['observed_guards']))

    def test_reject_refs_short_unknown_and_tree_ids(self):
        for value in ('HEAD', self.commit[:12], 'f'*40, C.git(self.root, 'rev-parse', 'HEAD^{tree}')):
            with self.subTest(value=value), self.assertRaises(C.S.R.RunnerError): self.build(value)

    def test_dirty_checkout_does_not_change_commit_bound_candidate(self):
        before = self.build()
        (self.root/C.ROOT_PATH/'kustomization.yaml').write_text('unsafe dirty working tree\n')
        self.assertEqual(self.build(), before)

    def test_scope_drift_paths_sources_duplicate_identity_and_pin_drift_rejected(self):
        original = copy.deepcopy(self.items)
        for change in ('escape', 'otherpath', 'source', 'duplicate', 'pin'):
            self.items = copy.deepcopy(original)
            k = next(i for i in self.items if i['kind'] == 'Kustomization')
            if change == 'escape': k['spec']['path'] = '../outside'
            if change == 'otherpath': k['spec']['path'] = './deploy/other'
            if change == 'source': k['spec']['sourceRef']['name'] = 'other'
            if change == 'duplicate': self.items.append(copy.deepcopy(self.items[0]))
            if change == 'pin':
                d = next(i for i in self.items if i['kind'] == 'Deployment')
                d['spec']['template']['spec']['containers'][0]['image'] = 'controller:latest'
            with self.subTest(change=change), self.assertRaises(C.S.R.RunnerError): self.build()

    def test_unsuspended_flux_jobs_writer_and_retained_spec_drift_rejected(self):
        original = copy.deepcopy(self.items)
        for kind in ('GitRepository', 'Kustomization', 'Job', 'PersistentVolumeClaim', 'Deployment'):
            self.items = copy.deepcopy(original)
            target = next(i for i in self.items if i['kind'] == kind and
                          (kind != 'Deployment' or i['metadata']['namespace'] == 'shirokuma-dev'))
            target['spec']['volumeName' if kind == 'PersistentVolumeClaim' else
                           'replicas' if kind == 'Deployment' else 'suspend'] = (
                               'other' if kind == 'PersistentVolumeClaim' else 1 if kind == 'Deployment' else False)
            with self.subTest(kind=kind), self.assertRaises(C.S.R.RunnerError): self.build()

    def test_missing_tree_and_blob_root_rejected(self):
        target = self.root/C.ROOT_PATH/'kustomization.yaml'
        target.unlink(); target.parent.rmdir()
        subprocess.run(['git', 'add', '-A'], cwd=self.root, check=True)
        subprocess.run(['git', '-c', 'user.name=Fixture', '-c', 'user.email=fixture@invalid',
                        'commit', '-qm', 'missing root'], cwd=self.root, check=True)
        with self.assertRaises(C.S.R.RunnerError): self.build(C.git(self.root, 'rev-parse', 'HEAD'))
        target.parent.write_text('not a directory\n')
        subprocess.run(['git', 'add', '-A'], cwd=self.root, check=True)
        subprocess.run(['git', '-c', 'user.name=Fixture', '-c', 'user.email=fixture@invalid',
                        'commit', '-qm', 'blob root'], cwd=self.root, check=True)
        with self.assertRaisesRegex(C.S.R.RunnerError, 'CANDIDATE_PATH_NOT_TREE'):
            self.build(C.git(self.root, 'rev-parse', 'HEAD'))

    def test_git_reads_disable_lazy_fetch_and_replacements(self):
        result = subprocess.CompletedProcess([], 0, b'commit\n', b'')
        with mock.patch.object(C.subprocess, 'run', return_value=result) as run:
            C.git(self.root, 'cat-file', '-t', self.commit)
        self.assertEqual(run.call_args.kwargs['env']['GIT_NO_LAZY_FETCH'], '1')
        self.assertEqual(run.call_args.kwargs['env']['GIT_NO_REPLACE_OBJECTS'], '1')
        self.assertIn('--no-replace-objects', run.call_args.args[0])

    def test_path_canonicalization_rejects_aliases_and_absolute_paths(self):
        for p in ('/root', '../root', 'a/../b', 'a//b', 'a/./b', './', '', 'a\\b', 'a:b'):
            with self.subTest(path=p), self.assertRaises(C.S.R.RunnerError): C.path(p)


if __name__ == '__main__': unittest.main()
