import unittest
from unittest import mock
from tools.local_experiment import flux_pin_candidate as P


class CandidateTests(unittest.TestCase):
    def fixture(self):
        images=[dict(component=x,version='v1.1.0') for x in sorted(P.S.FLUX)]
        refs=['ghcr.io/fluxcd/'+x['component']+':v1.2.0' for x in images]
        return ('\n'.join('image: '+ref for ref in refs)+'\n').encode(),dict(release='v2.9.6',references=refs),images

    def test_release_is_observation_not_pin_change(self):
        raw,meta,baseline=self.fixture()
        out=P.release_images(raw,meta,baseline)
        self.assertEqual({x['component'] for x in out},P.S.FLUX)
        self.assertTrue(all('reference' not in x for x in out))
        self.assertTrue(all(x['platform']=='linux/arm64' for x in out))

    def test_missing_major_prerelease_duplicate_foreign_and_downgrade_reject(self):
        raw,meta,baseline=self.fixture()
        cases=[(raw,dict(meta,release='v3.0.0')),(raw,dict(meta,release='v2.9.7-rc.1')),
            (raw,dict(meta,references=meta['references'][:3])),
            (raw.replace(b'v1.2.0',b'v1.0.0'),dict(meta,references=[r.replace('v1.2.0','v1.0.0') for r in meta['references']])),
            (raw.replace(b'fluxcd/source-controller',b'foreign/source-controller'),meta)]
        for body,metadata in cases:
            with self.assertRaises(P.R.RunnerError): P.release_images(body,metadata,baseline)

    def test_index_child_binding_is_unique_arm64_linux(self):
        value=dict(schemaVersion=2,mediaType='application/vnd.oci.image.index.v1+json',
            manifests=[dict(digest='sha256:'+'a'*64,platform=dict(os='linux',architecture='arm64')),
                       dict(digest='sha256:'+'b'*64,platform=dict(os='linux',architecture='amd64'))])
        self.assertEqual(P.arm64_descriptor(value),'sha256:'+'a'*64)
        for bad in (dict(value,manifests=value['manifests'][1:]),
                    dict(value,manifests=value['manifests']+[value['manifests'][0]]),
                    dict(value,mediaType='application/vnd.oci.image.manifest.v1+json')):
            with self.assertRaises(P.R.RunnerError): P.arm64_descriptor(bad)

    def test_plan_has_zero_network_and_model_calls(self):
        with mock.patch('sys.argv',['candidate']),mock.patch.object(P,'collect') as collect:
            self.assertEqual(P.main(),0)
            collect.assert_not_called()


if __name__=='__main__': unittest.main()
