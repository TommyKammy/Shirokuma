import io
from datetime import datetime,timezone
import copy
import json
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest import mock
from tools.local_experiment import flux_local_image as I


class ImageTests(unittest.TestCase):
    def fixture(self,path,entrypoint=None,duplicate=False):
        config=dict(architecture='arm64',os='linux',config=dict(User='65534:65534',
            Entrypoint=['source-controller'] if entrypoint is None else entrypoint,
            Labels={'org.opencontainers.image.version':'v1.9.6'}),rootfs={'diff_ids':['sha256:fixture']})
        manifest=[dict(Config='original.json',RepoTags=['upstream:tag'],Layers=['layer.tar'])]
        with tarfile.open(path,'w') as tar:
            for name,raw in (('original.json',json.dumps(config).encode()),
                             ('manifest.json',json.dumps(manifest).encode()),('layer.tar',b'unchanged')):
                info=tarfile.TarInfo(name);info.size=len(raw);tar.addfile(info,io.BytesIO(raw))
                if duplicate and name=='manifest.json':tar.addfile(info,io.BytesIO(raw))

    def test_local_attribution_preserves_layer_bytes_and_nonroot_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);source=root/'old.tar';out=root/'new.tar';self.fixture(source)
            value=I.relabel_archive(source,out,'a'*64)
            self.assertFalse(value['upstream_publisher_claim'])
            with tarfile.open(out) as tar:
                manifest=json.load(tar.extractfile('manifest.json'))
                config=json.load(tar.extractfile(manifest[0]['Config']))
                self.assertEqual(tar.extractfile('layer.tar').read(),b'unchanged')
                self.assertEqual(manifest[0]['RepoTags'],[I.TAG])
                self.assertEqual(config['config']['User'],'65534:65534')
                self.assertEqual(config['config']['Labels']['io.shiro.local.binary-sha256'],'a'*64)
                self.assertNotEqual(config['config']['Labels']['org.opencontainers.image.version'],'v1.9.6')
            self.assertEqual(out.stat().st_mode&0o777,0o600)
            with self.assertRaises(FileExistsError):I.relabel_archive(source,out,'a'*64)

    def test_changed_entrypoint_or_duplicate_members_reject(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            for name,kwargs in [('wrong',dict(entrypoint=['different'])),('duplicate',dict(duplicate=True))]:
                source=root/(name+'.tar');self.fixture(source,**kwargs)
                with self.assertRaises(I.R.RunnerError):I.relabel_archive(source,root/(name+'-out.tar'),'a'*64)

    def test_default_does_not_collect_or_request_models(self):
        with mock.patch('sys.argv',['image']),mock.patch.object(I,'collect') as collect:
            self.assertEqual(I.main(),0);collect.assert_not_called()

    def test_stdlib_version_forms_require_real_os_and_exact_binary_scope(self):
        now=datetime.now(timezone.utc)
        scanner={'Version':'1','VulnerabilityDB':{'UpdatedAt':now.isoformat()}}
        scan={'CreatedAt':now.isoformat(),'Trivy':{'Version':'1'},
            'Metadata':{'ImageConfig':{'architecture':'arm64','os':'linux'}},
            'Results':[{'Class':'os-pkgs','Packages':[{'Name':'alpine-baselayout'}]},
                {'Type':'gobinary','Target':'usr/local/bin/source-controller',
                 'Packages':[{'Name':'stdlib','Version':'v1.26.6'}],
                 'Vulnerabilities':[{'Severity':'MEDIUM'}]}]}
        self.assertEqual(I.scan_observation(scan,scanner,now.timestamp())['HIGH'],0)
        scan['Results'][1]['Packages'][0]['Version']='1.26.6'
        self.assertEqual(I.scan_observation(scan,scanner,now.timestamp())['MEDIUM'],1)
        for version in ('v1.26.5','v1.26.6-rc.1','1.26.60','unknown'):
            bad=copy.deepcopy(scan);bad['Results'][1]['Packages'][0]['Version']=version
            with self.assertRaises(I.R.RunnerError):I.scan_observation(bad,scanner,now.timestamp())
        for bad in (dict(scan,Results=scan['Results'][1:]),dict(scan,Results=scan['Results'][:1])):
            with self.assertRaises(I.R.RunnerError):I.scan_observation(bad,scanner,now.timestamp())


if __name__=='__main__':unittest.main()
