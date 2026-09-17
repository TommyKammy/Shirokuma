"""Offline boundaries for digest-only image cache preparation; no Docker/HTTP calls."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.local_experiment.spark import prepare as P


def manifest():
    sizes = [1] * 10 + [P.COMPRESSED_BYTES - 10]
    return dict(schemaVersion=2, mediaType='application/vnd.oci.image.manifest.v1+json',
                config=dict(digest=P.CONFIG_DIGEST, size=4096),
                layers=[dict(digest='sha256:' + f'{i:064x}', size=s,
                             mediaType='application/vnd.oci.image.layer.v1.tar+gzip') for i, s in enumerate(sizes)])


def image():
    return [dict(Id=P.DIGEST, Architecture='arm64', Os='linux', RepoDigests=['spark@' + P.DIGEST],
                 Descriptor=dict(digest=P.DIGEST),
                 Config=dict(User='spark', Entrypoint=['/opt/entrypoint.sh'], WorkingDir='/opt/spark/work-dir',
                             Env=['SPARK_HOME=/opt/spark']), RootFS=dict(Layers=['sha256:' + '1' * 64] * 11),
                 Size=2 * 1024**3)]


def content():
    i = image()[0]
    return {'manifest': manifest(), 'config': {'architecture': 'arm64', 'os': 'linux',
            'rootfs': {'diff_ids': i['RootFS']['Layers']}, 'config': i['Config']}}


class Fake:
    def __init__(self):
        self.calls = []; self.pulled = False; self.preexisting = False
        self.free_kib = 8 * 1024**2; self.change_old = False; self.cri_id = P.DIGEST
        self.profile = 'Running'; self.node_runtime = 'docker://28.4.0'; self.host = str(Path.home() / '.colima/mac-studio-solo/docker.sock')
        self.node_uid = '00000000-0000-4000-8000-000000000000'; self.pull_error = None
    def __call__(self, args, **kwargs):
        self.calls.append(args)
        if args == ['colima', 'list', '--json']:
            return json.dumps(dict(name=P.PROFILE, status=self.profile, arch='aarch64', runtime='docker+k3s'))
        if args[:3] == ['docker', '--context', P.CONTEXT]:
            tail = args[3:]
            if tail[:2] == ['context', 'inspect']:
                return json.dumps([dict(Name=P.CONTEXT, Endpoints={'docker': {'Host': 'unix://' + self.host}})])
            if tail[:1] == ['info']:
                return json.dumps(dict(Architecture='aarch64', OSType='linux', DockerRootDir='/var/lib/docker', ID='owned',
                                       DriverStatus=[['driver-type', 'io.containerd.snapshotter.v1']]))
            if tail[:2] == ['image', 'ls']:
                old = dict(ID='sha256:' + '0' * 64, Repository='retained', Tag='fixed', Digest='sha256:' + '2' * 64)
                rows = [] if self.pulled and self.change_old else [old]
                if self.pulled or self.preexisting:
                    rows.append(dict(ID=P.DIGEST, Repository='spark', Tag='<none>', Digest=P.DIGEST))
                return '\n'.join(json.dumps(x) for x in rows)
            if tail[:1] == ['pull']:
                if self.pull_error:
                    raise self.pull_error
                self.pulled = True
                return 'pulled'
            if tail[:2] == ['image', 'inspect']:
                return json.dumps(image())
        if args[:2] == ['kubectl', '--context']:
            if 'config' in args:
                return json.dumps({'clusters': [{'cluster': {'server': 'https://127.0.0.1:6443'}}]})
            return json.dumps({'items': [{'metadata': {'name': P.CONTEXT, 'uid': self.node_uid}, 'status': {'nodeInfo': {
                'architecture': 'arm64', 'operatingSystem': 'linux', 'kubeletVersion': 'v1.33.4+k3s1',
                'containerRuntimeVersion': self.node_runtime}}}]})
        if args[:5] == ['colima', '--profile', P.PROFILE, 'ssh', '--']:
            if args[5] == 'df':
                return f'Filesystem 1024-blocks Used Available Capacity Mounted on\n/dev/vdb1 20000000 1000000 {self.free_kib} 5% /var/lib/docker\n'
            if 'inspecti' in args:
                return json.dumps({'status': {'id': self.cri_id, 'repoDigests': ['spark@' + P.DIGEST], 'size': '2147483648'}})
        raise AssertionError(args)


class SparkPrepareTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.output = Path(self.tmp.name).resolve()
        self.fake = Fake()
    def tearDown(self):
        self.tmp.cleanup()
    def prepare(self):
        with patch.object(P, 'cache_content', return_value=content()):
            return P.prepare(self.output, run=self.fake, registry=lambda: P.validate_manifest(manifest()))[0]
    def pulls(self):
        return [x for x in self.fake.calls if 'pull' in x]

    def test_digest_pull_cri_exact_reference_and_retained_inventory(self):
        result = self.prepare()
        self.assertEqual(result['result'], 'passed')
        self.assertEqual(self.pulls(), [P.docker('pull', '--platform', 'linux/arm64', P.IMAGE)])
        self.assertTrue(result['existing_images_unchanged'])
        self.assertTrue(result['image_evidence']['exact_reference_resolved'])
        self.assertFalse(result['workload_started'])
        self.assertTrue(any(x[-1] == P.IMAGE and 'inspecti' in x for x in self.fake.calls))
        self.assertFalse(any(set(x) & {'create', 'run', 'rm', 'rmi', 'import', 'save', 'tag'} for x in self.fake.calls))

    def test_cache_hit_performs_no_pull(self):
        self.fake.preexisting = True
        self.assertTrue(self.prepare()['already_cached'])
        self.assertEqual(self.pulls(), [])

    def test_insufficient_capacity_stops_before_registry_or_pull(self):
        self.fake.free_kib = 100
        with patch.object(P, 'official_manifest', side_effect=AssertionError('HTTP prohibited')):
            with self.assertRaisesRegex(P.ExperimentError, 'INSUFFICIENT_IMAGE_HEADROOM'):
                self.prepare()
        self.assertEqual(self.pulls(), [])

    def test_other_context_rejected_before_pull(self):
        self.fake.host = '/tmp/other.sock'
        with self.assertRaisesRegex(P.ExperimentError, 'DOCKER_CONTEXT_MISMATCH'):
            self.prepare()
        self.assertEqual(self.pulls(), [])

    def test_stopped_profile_rejected(self):
        self.fake.profile = 'Stopped'
        with self.assertRaisesRegex(P.ExperimentError, 'COLIMA_PROFILE_MISMATCH'):
            self.prepare()
        self.assertEqual(self.pulls(), [])

    def test_containerd_requires_different_preparation_path(self):
        self.fake.node_runtime = 'containerd://2.0.0'
        with self.assertRaisesRegex(P.ExperimentError, 'NODE_RUNTIME_MISMATCH'):
            self.prepare()
        self.assertEqual(self.pulls(), [])

    def test_existing_image_disappearance_cannot_pass(self):
        self.fake.change_old = True
        with self.assertRaisesRegex(P.ExperimentError, 'EXISTING_IMAGES_CHANGED'):
            self.prepare()

    def test_cri_reference_to_other_config_cannot_pass(self):
        self.fake.cri_id = 'sha256:' + 'f' * 64
        with self.assertRaisesRegex(P.ExperimentError, 'CRI_IMAGE_STATUS_MISMATCH'):
            self.prepare()

    def test_interrupted_pull_keeps_pending_receipt_no_retry(self):
        self.fake.pull_error = KeyboardInterrupt()
        with self.assertRaises(KeyboardInterrupt):
            self.prepare()
        saved = json.loads((self.output / 'summary.json').read_bytes())
        self.assertEqual(saved['result'], 'failed')
        self.assertEqual(saved['pending'], {'operation': 'pull', 'image': P.IMAGE})
        self.assertEqual(len(self.pulls()), 1)

    def test_reusing_evidence_directory_rejected_before_commands(self):
        self.prepare(); self.fake.calls.clear()
        with self.assertRaisesRegex(P.ExperimentError, 'OUTPUT_ALREADY_USED'):
            self.prepare()
        self.assertEqual(self.fake.calls, [])

    def test_native_arch_and_config_checked_independently_of_manifest(self):
        for key, value in [('Architecture', 'amd64'), ('Id', 'sha256:' + '3' * 64), ('Os', 'windows')]:
            with self.subTest(key=key):
                candidate = image(); candidate[0][key] = value
                with self.assertRaisesRegex(P.ExperimentError, 'CACHED_IMAGE_IDENTITY'):
                    P.validate_cache(candidate, {}, content())

    def test_cached_config_must_equal_inspected_native_config(self):
        c = content(); c['config']['architecture'] = 'amd64'
        with self.assertRaisesRegex(P.ExperimentError, 'CACHED_CONFIG_CONTENT_MISMATCH'):
            P.validate_cache(image(), {}, c)

    def test_only_known_absent_oci_fields_may_be_docker_null_defaults(self):
        candidate = image(); candidate[0]['Config'].update(Cmd=None, OnBuild=None, Volumes=None)
        cri = {'status': {'id': P.DIGEST, 'repoDigests': ['spark@' + P.DIGEST], 'size': '2147483648'}}
        self.assertTrue(P.validate_cache(candidate, cri, content())['exact_reference_resolved'])
        candidate[0]['Config']['Volumes'] = {'/foreign': {}}
        with self.assertRaisesRegex(P.ExperimentError, 'CACHED_CONFIG_CONTENT_MISMATCH'):
            P.validate_cache(candidate, cri, content())

    def test_raw_cache_content_is_hashed_not_trusted_by_claim(self):
        with self.assertRaisesRegex(P.ExperimentError, 'CACHED_CONTENT_HASH_MISMATCH'):
            P.cache_content(lambda *a, **kw: json.dumps(manifest()))

    def test_legacy_config_id_is_not_mistaken_for_manifest_id(self):
        candidate = image(); candidate[0]['Id'] = P.CONFIG_DIGEST
        with self.assertRaisesRegex(P.ExperimentError, 'CACHED_IMAGE_IDENTITY'):
            P.validate_cache(candidate, {}, content())

    def test_manifest_pinned_config_layer_count_sizes_and_types(self):
        invalid = []
        value = manifest(); value['config']['digest'] = 'sha256:' + '0' * 64; invalid.append(value)
        value = manifest(); value['layers'].pop(); invalid.append(value)
        value = manifest(); value['layers'][0]['size'] = True; invalid.append(value)
        value = manifest(); value['layers'][0]['size'] += 1; invalid.append(value)
        value = manifest(); value['layers'][0]['mediaType'] = 'application/x-foreign'; invalid.append(value)
        for value in invalid:
            with self.assertRaises(P.ExperimentError):
                P.validate_manifest(value)

    def test_registry_redirect_never_forwards_authentication(self):
        with self.assertRaisesRegex(P.ExperimentError, 'REGISTRY_REDIRECT_REJECTED'):
            P.NoRedirect().redirect_request(None, None, 302, None, None, 'https://other.invalid')

    def test_manifest_hash_verified_before_parsing(self):
        class Response:
            status = 200
            def __init__(self, url, value): self.url, self.value = url, value
            def geturl(self): return self.url
            def read(self, limit): return self.value
            def __enter__(self): return self
            def __exit__(self, *args): pass
        class Opener:
            def open(self, request, **kwargs):
                if request.full_url.startswith('https://auth.docker.io/'):
                    return Response(request.full_url, b'{"token":"anonymous-not-saved"}')
                return Response(request.full_url, json.dumps(manifest()).encode())
        with patch.object(P.urllib.request, 'build_opener', return_value=Opener()):
            with self.assertRaisesRegex(P.ExperimentError, 'MANIFEST_DIGEST_MISMATCH'):
                P.official_manifest()


if __name__ == '__main__':
    unittest.main()
