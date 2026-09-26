import base64
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


if __name__=='__main__':unittest.main()
