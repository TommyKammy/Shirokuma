import base64
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from tools.local_experiment.finops import linux_volume_ssh_contract as S


KEY=base64.b64encode((11).to_bytes(4,'big')+b'ssh-ed25519'+
                     (32).to_bytes(4,'big')+b'X'*32).decode()


class SSHContractTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.known=Path(self.temp.name).resolve()/'hosts'
        self.known.write_text(f'{S.ALIAS} ssh-ed25519 {KEY}\n');self.known.chmod(0o600)
        self.identity=Path(self.temp.name).resolve()/'client'
        self.identity.write_bytes(b'test-only');self.identity.chmod(0o600)

    def build(self,**changes):
        value={'port':61292,'user':'jp','known_hosts':self.known,
               'identity_file':self.identity,'enrolled_key':KEY}
        value.update(changes)
        return S.strict_ssh_prefix(**value)

    def test_offline_builder_disables_unauthenticated_routing_and_prompts(self):
        argv=self.build()
        self.assertEqual(argv[:3],['ssh','-F','/dev/null'])
        self.assertEqual(argv[-1],'jp@127.0.0.1')
        text=' '.join(argv)
        for required in ('StrictHostKeyChecking=yes','GlobalKnownHostsFile=/dev/null',
                         'BatchMode=yes','PasswordAuthentication=no','IdentityAgent=none',
                         'ProxyCommand=none','ControlMaster=no','UpdateHostKeys=no'):
            self.assertIn(required,text)
        self.assertNotIn('accept-new',text)

    @unittest.skipUnless(shutil.which('ssh'),'OpenSSH -G required for local config parsing')
    def test_openssh_parses_the_pinned_literal_paths_offline(self):
        argv=self.build()
        result=subprocess.run([argv[0],'-G',*argv[1:]],stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE,timeout=5,check=True)
        self.assertLessEqual(len(result.stdout),32768)
        lines=result.stdout.decode().splitlines()
        self.assertIn('hostname 127.0.0.1',lines)
        self.assertIn('hostkeyalias '+S.ALIAS,lines)
        self.assertIn('userknownhostsfile '+str(self.known),lines)
        self.assertIn('stricthostkeychecking true',lines)
        self.assertIn('batchmode yes',lines)

    def test_missing_or_mismatched_enrollment_refused_before_any_ssh(self):
        for changed in ({'port':0},{'user':'-oProxyCommand=bad'},{'enrolled_key':'a'},
                        {'identity_file':self.known/'sub'}):
            with self.subTest(change=changed),self.assertRaises((S.EndpointUntrusted,OSError)):
                self.build(**changed)
        self.known.write_text('foreign.example ssh-ed25519 '+KEY+'\n')
        with self.assertRaises(S.EndpointUntrusted):self.build()
        self.known.write_text(f'{S.ALIAS} ssh-ed25519 {KEY}\n');self.known.chmod(0o666)
        with self.assertRaises(S.EndpointUntrusted):self.build()
        self.known.chmod(0o600)
        self.known.write_bytes(b'x'*2048)
        with self.assertRaises(S.EndpointUntrusted):self.build()
        self.known.write_text(f'{S.ALIAS} ssh-ed25519 {KEY}\n')
        for unsafe in ('bad key','bad%key','bad${HOME}','quote"','bad\nkey'):
            with self.subTest(unsafe=unsafe),self.assertRaises(S.EndpointUntrusted):
                self.build(known_hosts=self.known.with_name(unsafe))

    def test_anonymous_snapshot_survives_enrollment_path_replacement(self):
        with S.pinned_ssh_prefix(port=61292,user='jp',known_hosts=self.known,
                                 identity_file=self.identity,enrolled_key=KEY) as pin:
            fd,=pin.pass_fds
            self.assertIn('UserKnownHostsFile=/dev/fd/'+str(fd),pin.argv)
            self.assertEqual(os.fstat(fd).st_nlink,0)
            self.assertEqual(os.lseek(fd,0,os.SEEK_CUR),0)
            self.known.rename(self.known.with_name('old-hosts'))
            self.known.write_text('evil.example ssh-ed25519 '+KEY+'\n')
            child=subprocess.run(['python3','-c',
                'import os,sys; sys.stdout.buffer.write(open(sys.argv[1],"rb").read())',
                '/dev/fd/'+str(fd)],pass_fds=pin.pass_fds,stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,timeout=5,check=True)
            self.assertEqual(child.stdout,f'{S.ALIAS} ssh-ed25519 {KEY}\n'.encode())
            # /dev/fd duplicates the open description; child reads advance the
            # shared offset. A real OpenSSH handshake still needs verification.
            self.assertEqual(os.lseek(fd,0,os.SEEK_CUR),len(child.stdout))
        with self.assertRaises(OSError):os.fstat(fd)

    @unittest.skipUnless(shutil.which('ssh'),'OpenSSH -G required for local config parsing')
    def test_snapshot_requires_preexisting_exact_enrollment_and_private_files(self):
        def pin():
            return S.pinned_ssh_prefix(port=61292,user='jp',known_hosts=self.known,
                                       identity_file=self.identity,enrolled_key=KEY)
        self.known.write_text('evil.example ssh-ed25519 '+KEY+'\n')
        with self.assertRaises(S.EndpointUntrusted),pin():pass
        self.known.write_text(f'{S.ALIAS} ssh-ed25519 {KEY}\n')
        self.identity.chmod(0o666)
        with self.assertRaises(S.EndpointUntrusted),pin():pass
        self.identity.chmod(0o600)
        with pin() as snapshot:
            fd,=snapshot.pass_fds
            result=subprocess.run(['ssh','-G',*snapshot.argv[1:]],pass_fds=snapshot.pass_fds,
                                  stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=5,check=True)
            self.assertIn(('userknownhostsfile /dev/fd/'+str(fd)).encode(),result.stdout)


if __name__=='__main__':unittest.main()
