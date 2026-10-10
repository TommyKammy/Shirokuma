import gzip
import io
import json
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest import mock
from tools.local_experiment import kustomize_os_image as I


class ImageTests(unittest.TestCase):
    def blob(self,name=I.BINARY,arch=183,kind=tarfile.REGTYPE):
        header=bytearray(64);header[:6]=b'\x7fELF\x02\x01';header[18:20]=arch.to_bytes(2,'little')
        stream=io.BytesIO()
        with tarfile.open(fileobj=stream,mode='w',format=tarfile.USTAR_FORMAT) as tar:
            item=tarfile.TarInfo(name);item.mode=0o755;item.type=kind
            item.size=len(header) if kind==tarfile.REGTYPE else 0
            tar.addfile(item,io.BytesIO(header) if item.size else None)
        plain=stream.getvalue();blob=gzip.compress(plain,mtime=0)
        return blob,dict(digest='sha256:'+I.L.apk.digest(blob),size=len(blob)),'sha256:'+I.L.apk.digest(plain)

    def test_controller_exact_static_elf_not_execution(self):
        value=I.controller(*self.blob())
        self.assertFalse(value['executed']);self.assertFalse(value['go_binary_patched'])
        self.assertEqual(value['binary_bytes'],64)

    def test_controller_wrong_arch_extra_path_link_and_blob_tampering_hold(self):
        for args in (dict(arch=62),dict(name='lib/apk/db/installed'),dict(kind=tarfile.SYMTYPE)):
            with self.assertRaises(I.L.Error):I.controller(*self.blob(**args))
        blob,desc,diff=self.blob()
        with self.assertRaises(I.L.Error):I.controller(blob+b'x',desc,diff)
        with self.assertRaises(I.L.Error):I.controller(blob,desc,'sha256:'+'0'*64)

    def test_metadata_preserves_runtime_config_but_never_claims_go_or_publisher(self):
        original={'architecture':'arm64','os':'linux','config':{'Entrypoint':['kustomize-controller'],
            'User':'65534:65534','Env':['SAME=1']},'rootfs':{'type':'layers','diff_ids':['sha256:'+'a'*64]*3}}
        result=json.loads(I.metadata(json.dumps(original).encode(),b'patch'))
        self.assertEqual(result['config']['Env'],original['config']['Env'])
        self.assertEqual(len(result['rootfs']['diff_ids']),4)
        self.assertEqual(len(original['rootfs']['diff_ids']),3)
        self.assertEqual(result['config']['Labels']['io.shiro.local.go-binary-patched'],'false')

    def test_wrong_user_or_entrypoint_rejected(self):
        for config in ({'Entrypoint':['sh'],'User':'65534:65534'},
                       {'Entrypoint':['kustomize-controller'],'User':'0'}):
            with self.assertRaises(I.L.Error):
                I.metadata(json.dumps({'architecture':'arm64','os':'linux','config':config}).encode(),b'p')

    def test_assembly_binds_prefix_before_any_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'image.tar'
            with mock.patch.object(I.L,'validate_candidate_patch',side_effect=I.L.Error('prefix proof missing')):
                with self.assertRaises(I.L.Error):
                    I.assemble(path,b'',b'',[b'a',b'b',b'c'],b'p',{},b'',b'',b'','0'*64,b'')
            self.assertFalse(path.exists())

    def test_successful_archive_is_unsigned_os_only_and_exclusive(self):
        blob,desc,diff=self.blob()
        config={'architecture':'arm64','os':'linux','config':{
            'Entrypoint':['kustomize-controller'],'User':'65534:65534'},
            'rootfs':{'type':'layers','diff_ids':['sha256:'+'a'*64,'sha256:'+'b'*64,diff]}}
        manifest=json.dumps({'layers':[{}, {}, desc]}).encode()
        inputs=(manifest,json.dumps(config).encode(),[b'os1',b'os2',blob],b'patch',{},
                b'',b'',b'','0'*64,b'')
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp).resolve();root.chmod(0o700);path=root/'image.tar'
            with mock.patch.object(I.L,'validate_candidate_patch'):
                report=I.assemble(path,*inputs)
                with self.assertRaises(FileExistsError):I.assemble(path,*inputs)
            self.assertEqual(path.stat().st_mode&0o777,0o600)
            self.assertFalse(report['go_binary_patched']);self.assertFalse(report['image_executed'])
            with tarfile.open(path) as archive:
                saved=json.load(archive.extractfile('manifest.json'))[0]
                self.assertEqual(len(saved['Layers']),4)
                self.assertEqual(saved['RepoTags'],[I.TAG])


if __name__=='__main__':unittest.main()
