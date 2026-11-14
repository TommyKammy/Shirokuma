"""Canonical coordinator-profile attestation through the mock provider boundary."""
import copy
from pathlib import Path
import unittest
from unittest.mock import patch

import test_logical_foundation as foundation_tests
import test_logical_provider as provider_tests
from tools.local_experiment import logical_foundation as f
from tools.local_experiment import logical_provider as p


class ProfileHarness(provider_tests.Harness):
    def __init__(self, config):
        super().__init__(config)
        public, _ = f.render(config)
        self.rendered = {(doc['kind'].lower(), doc['metadata']['name']): doc for doc in public}
        self.mutate = None

    def resource(self, kind, name):
        result = super().resource(kind, name)
        if kind == 'deployment' and name == 'trino-local':
            result['spec']['template'] = copy.deepcopy(self.rendered[kind, name]['spec']['template'])
        elif kind == 'pod' and name == 'trino-local-pod':
            template = self.rendered['deployment', 'trino-local']['spec']['template']
            result['spec']['containers'][0]['resources'] = copy.deepcopy(template['spec']['containers'][0]['resources'])
            result['metadata']['annotations'] = copy.deepcopy(template['metadata']['annotations'])
        elif kind == 'configmap' and name == self.c['prefix']+'-trino-config':
            result['data'] = copy.deepcopy(self.rendered[kind, name]['data'])
        if self.mutate:
            self.mutate(kind, name, result)
        return result


class CoordinatorProfileTests(unittest.TestCase):
    def setUp(self):
        base = provider_tests.ProviderTests()
        base.setUp()
        self.addCleanup(base.doCleanups)
        self.c, self.root = base.c, base.root
        fixture = foundation_tests.FoundationTests()
        fixture.root = self.root
        (self.root/'logical-secrets.jar').unlink()
        fixture.fixture()
        # The provider harness attests this standard SPI fixture as b'code'.
        (self.root/'logical-secrets.jar').unlink()
        f.save_private(self.root/'logical-secrets.jar', b'code')
        self.c['trino']['coordinator'] = {
            'cpu_request_millicores': 1500, 'cpu_limit_millicores': 3000,
            'memory_request_mib': 3072, 'memory_limit_mib': 5120, 'heap_mib': 2560}

    def provider(self, workers=0, *, explicit=True):
        config = copy.deepcopy(self.c)
        config['trino']['workers'] = workers
        if explicit:
            harness = ProfileHarness(config)
        else:
            del config['trino']['coordinator']
            harness = provider_tests.Harness(config)
        provider = p.Provider(config, command=harness.command, http=harness.http,
            sdk=harness.sdk, popen=harness.popen)
        self.addCleanup(provider.close)
        return provider, harness

    def bind(self, provider):
        provider.open()
        receipt = provider.probe()
        self.assertTrue(receipt['ready'])
        provider.bind_registered(receipt)
        self.assertTrue(provider.bound_alive())

    def test_nondefault_profile_compute_probe_bind_and_live_health(self):
        for workers in (0, 1):
            with self.subTest(workers=workers):
                provider, harness = self.provider(workers)
                self.assertEqual(harness.commands, [])
                self.assertEqual(len(provider._compute()), 1+workers)
                self.bind(provider)
                self.assertTrue(provider.live_health()['ready'])

    def test_legacy_saved_probe_binds_explicit_profile_without_new_pins(self):
        key = 'configmap/'+self.c['prefix']+'-trino-config'
        for workers in (0, 1):
            with self.subTest(workers=workers):
                legacy, _ = self.provider(workers, explicit=False)
                legacy.open()
                saved = legacy.probe()
                frozen = copy.deepcopy(saved)
                self.assertTrue(saved['ready'])
                self.assertEqual({pin for pin in saved['incarnation'] if pin.startswith('configmap/')},
                    {'configmap/'+self.c['prefix']+'-secrets-code'})
                provider, _ = self.provider(workers)
                provider.open()
                provider.bind_registered(saved)
                self.assertTrue(provider.bound_alive())
                self.assertTrue(provider.live_health()['ready'])
                actual = provider.probe()
                self.assertTrue(actual['ready'])
                for pins in (actual['incarnation'], provider._incarnation, provider._bound_pins):
                    self.assertNotIn(key, pins)
                self.assertEqual(saved, frozen)

    def test_coordinator_material_requires_owned_exact_metadata(self):
        name = self.c['prefix']+'-trino-config'
        cases = ((f.LABEL, 'FOUNDATION_OWNERSHIP_MISMATCH'),
            (f.REVISION, 'FOUNDATION_OWNERSHIP_MISMATCH'),
            ('uid', 'FOUNDATION_OWNERSHIP_MISMATCH'),
            ('name', 'FOUNDATION_OWNERSHIP_MISMATCH'),
            ('namespace', 'FOUNDATION_NAMESPACE_MISMATCH'))
        for field, reason in cases:
            for missing in (False, True):
                with self.subTest(field=field, missing=missing):
                    provider, harness = self.provider()
                    def mutate(kind, actual_name, result):
                        if (kind, actual_name) != ('configmap', name):
                            return
                        values = result['metadata']['labels'] if field in (f.LABEL, f.REVISION) else result['metadata']
                        if missing:
                            del values[field]
                        else:
                            values[field] = '' if field == 'uid' else 'foreign'
                    harness.mutate = mutate
                    before = (copy.deepcopy(provider._incarnation), copy.deepcopy(provider._bound_pins))
                    deployment = harness.resource('deployment', 'trino-local')
                    with self.assertRaisesRegex(p.Rejected, reason):
                        provider._coordinator_profile(deployment)
                    self.assertEqual((provider._incarnation, provider._bound_pins), before)

    def test_coordinator_material_enforces_both_existing_pins_without_mutation(self):
        name = self.c['prefix']+'-trino-config'
        key = 'configmap/'+name
        for incarnation, bound in ((None, None), ('configmap-uid', None),
                (None, 'configmap-uid'), ('configmap-uid', 'configmap-uid'),
                ('old-uid', None), (None, 'old-uid'),
                ('old-uid', 'configmap-uid'), ('configmap-uid', 'old-uid')):
            with self.subTest(incarnation=incarnation, bound=bound):
                provider, harness = self.provider()
                if incarnation is not None:
                    provider._incarnation[key] = incarnation
                if bound is not None:
                    provider._bound_pins[key] = bound
                before = (copy.deepcopy(provider._incarnation), copy.deepcopy(provider._bound_pins))
                deployment = harness.resource('deployment', 'trino-local')
                with patch.object(provider, '_owned', side_effect=AssertionError('material must not enroll')):
                    if 'old-uid' in (incarnation, bound):
                        with self.assertRaisesRegex(p.Rejected, 'FOUNDATION_INCARNATION_CHANGED'):
                            provider._coordinator_profile(deployment)
                    else:
                        self.assertIsNotNone(provider._coordinator_profile(deployment))
                self.assertEqual((provider._incarnation, provider._bound_pins), before)

    def test_each_deployment_and_pod_resource_field_must_match(self):
        for kind in ('deployment', 'pod'):
            for section in ('requests', 'limits'):
                for field in ('cpu', 'memory'):
                    for missing in (False, True):
                        with self.subTest(kind=kind, section=section, field=field, missing=missing):
                            provider, harness = self.provider()
                            def mutate(actual_kind, name, result):
                                if actual_kind != kind or name not in ('trino-local', 'trino-local-pod'):
                                    return
                                spec = result['spec']['template']['spec'] if kind == 'deployment' else result['spec']
                                values = spec['containers'][0]['resources'][section]
                                if missing:
                                    del values[field]
                                else:
                                    values[field] = '1'
                            harness.mutate = mutate
                            with self.assertRaisesRegex(p.Rejected, 'FOUNDATION_COMPUTE_MISMATCH'):
                                provider._compute()

    def test_template_and_pod_annotations_required(self):
        key = 'shirokuma.dev/coordinator-resources'
        for kind in ('deployment', 'pod'):
            for missing in (False, True):
                with self.subTest(kind=kind, missing=missing):
                    provider, harness = self.provider()
                    def mutate(actual_kind, name, result):
                        if actual_kind != kind or name not in ('trino-local', 'trino-local-pod'):
                            return
                        metadata = result['spec']['template']['metadata'] if kind == 'deployment' else result['metadata']
                        if missing:
                            del metadata['annotations'][key]
                            # A deployment-level annotation is not a template annotation.
                            if kind == 'deployment':
                                result['metadata'].setdefault('annotations', {})[key] = 'wrong-location'
                        else:
                            metadata['annotations'][key] = 'wrong'
                    harness.mutate = mutate
                    with self.assertRaisesRegex(p.Rejected, 'FOUNDATION_COMPUTE_MISMATCH'):
                        provider._compute()

    def test_jvm_config_exact_match_and_presence(self):
        for missing in (False, True):
            with self.subTest(missing=missing):
                provider, harness = self.provider()
                def mutate(kind, name, result):
                    if kind == 'configmap' and name == self.c['prefix']+'-trino-config':
                        if missing:
                            del result['data']['jvm.config']
                        else:
                            result['data']['jvm.config'] += '\n'
                harness.mutate = mutate
                with self.assertRaisesRegex(p.Rejected, 'FOUNDATION_CONFIG_MISMATCH'):
                    provider._compute()

    def test_ready_status_does_not_bypass_profile_drift(self):
        provider, harness = self.provider(1)
        self.bind(provider)
        def mutate(kind, name, result):
            if kind == 'pod' and name == 'trino-local-pod':
                result['spec']['containers'][0]['resources']['requests']['cpu'] = '1'
        harness.mutate = mutate
        self.assertEqual(provider.compute_state(), 'ACTIVE')
        self.assertFalse(provider.probe()['ready'])
        self.assertFalse(provider.live_health()['ready'])

    def test_hotpath_uses_pure_helper_once_without_render_or_private_io(self):
        provider, harness = self.provider(1)
        with patch.object(f, 'render', side_effect=AssertionError('render on hotpath')), \
                patch.object(f, 'private_read', side_effect=AssertionError('private read on hotpath')), \
                patch.object(Path, 'read_bytes', side_effect=AssertionError('jar read on hotpath')), \
                patch.object(f, 'coordinator_profile', wraps=f.coordinator_profile) as profile:
            self.assertEqual(len(provider._compute()), 2)
            profile.assert_called_once_with(provider._config)
        self.assertFalse(any('exec' in argv for argv, _ in harness.commands))

    def test_legacy_compute_does_not_render_call_helper_or_get_configmap(self):
        for workers in (0, 1):
            with self.subTest(workers=workers):
                provider, harness = self.provider(workers, explicit=False)
                with patch.object(f, 'render', side_effect=AssertionError('legacy render')), \
                        patch.object(f, 'coordinator_profile', side_effect=AssertionError('legacy helper')):
                    self.assertEqual(len(provider._compute()), 1+workers)
                self.assertFalse(any('configmap' in argv or 'exec' in argv for argv, _ in harness.commands))

    def test_unrelated_kubernetes_defaults_are_accepted(self):
        provider, harness = self.provider()
        def mutate(kind, name, result):
            if kind == 'deployment' and name == 'trino-local':
                result = result['spec']['template']
            elif kind != 'pod' or name != 'trino-local-pod':
                return
            result['metadata']['annotations']['other.example/default'] = 'yes'
            result['spec']['containers'][0]['resources']['requests']['ephemeral-storage'] = '1Gi'
        harness.mutate = mutate
        self.assertEqual(len(provider._compute()), 1)

    def test_existing_security_and_mount_guards_still_reject_drift(self):
        for drift in ('security', 'mount'):
            with self.subTest(drift=drift):
                provider, harness = self.provider(1)
                def mutate(kind, name, result):
                    if kind == 'pod' and name == 'trino-local-pod':
                        container = result['spec']['containers'][0]
                        if drift == 'security':
                            container['securityContext']['allowPrivilegeEscalation'] = True
                        else:
                            container['volumeMounts'][0]['subPath'] = 'bypass'
                harness.mutate = mutate
                with self.assertRaisesRegex(p.Rejected, 'FOUNDATION_COMPUTE_MISMATCH'):
                    provider._compute()

    def test_explicit_profile_stopped_bind_uses_canonical_specs(self):
        import test_logical_compute_lifecycle as lifecycle

        fixture = lifecycle.StoppedProofTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        fixture.c['trino']['coordinator'] = copy.deepcopy(self.c['trino']['coordinator'])
        public, private = f.render(fixture.c)
        fixture.h.documents = {(doc['kind'].lower(), doc['metadata']['name']): doc
            for doc in public + private}
        provider = p.Provider(fixture.c, command=fixture.h.command, http=fixture.h.http,
            sdk=fixture.h.sdk, popen=fixture.h.popen)
        self.addCleanup(provider.close)
        provider.bind_stopped_registered(fixture.saved)
        self.assertFalse(provider.bound_alive())
        self.assertFalse(provider._opened)
        self.assertEqual(fixture.h.http_calls, [])
        self.assertEqual(fixture.h.sdk_calls, [])
        self.assertTrue(all('get' in argv or 'config' in argv for argv, _ in fixture.h.commands))
        def drift(kind, name, result):
            if kind == 'deployment' and name == 'trino-local':
                del result['spec']['template']['metadata']['annotations']['shirokuma.dev/coordinator-resources']
        fixture.h.change = drift
        with self.assertRaisesRegex(p.Rejected, 'FOUNDATION_SPEC_MISMATCH'):
            provider.bind_stopped_registered(fixture.saved)
