import importlib.util
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "verify_supply_chain", ROOT / "scripts" / "verify_supply_chain.py"
)
supply_chain = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(supply_chain)

HEADER = "-----BEGIN " + "PRIVATE KEY-----"
FOOTER = "-----END " + "PRIVATE KEY-----"
BODY_LINE = "MIIEvQIBADANBgkqhkiG9w0BAQEFAASCBKcwggSjAgEAAoIBAQC7"


class SecretScanTests(unittest.TestCase):
    def scan(self, files):
        with tempfile.TemporaryDirectory() as directory:
            repository = Path(directory)
            subprocess.run(["git", "init", "-q"], cwd=repository, check=True)
            for name, content in files.items():
                path = repository / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(content, encoding="utf-8")
            subprocess.run(["git", "add", "."], cwd=repository, check=True)
            supply_chain.scan_secrets(repository)

    def assertRejected(self, content):
        with self.assertRaises(supply_chain.PolicyError):
            self.scan({"leak.txt": content})

    def test_wrapped_private_key_is_rejected(self):
        self.assertRejected(f"{HEADER}\n{BODY_LINE}\n{BODY_LINE}\n{FOOTER}\n")

    def test_single_line_and_escaped_private_keys_are_rejected(self):
        self.assertRejected(f"{HEADER}\n{BODY_LINE}{BODY_LINE}\n{FOOTER}\n")
        self.assertRejected(f'"{HEADER}\\n{BODY_LINE}\\n{BODY_LINE}\\n{FOOTER}"')

    def test_rsa_and_openssh_private_keys_are_rejected(self):
        for kind in ("RSA ", "OPENSSH "):
            with self.subTest(kind=kind):
                header = "-----BEGIN " + kind + "PRIVATE KEY-----"
                self.assertRejected(f"{header}\n{BODY_LINE}\n{BODY_LINE}\n")

    def test_header_used_for_format_checks_is_allowed(self):
        self.scan({
            "check.py": f"ok = pem.startswith('{HEADER}') and pem.endswith('{FOOTER}')\n",
            "check.tf": f'startswith(trimspace(pem), "{HEADER}")\n',
        })

    def test_synthetic_key_in_test_data_is_allowed(self):
        self.scan({"fixture.py": f"key = '{HEADER}\\nSYNTHETIC-1\\n{FOOTER}\\n'\n"})

    def test_access_keys_and_secret_filenames_are_still_rejected(self):
        with self.assertRaises(supply_chain.PolicyError):
            self.scan({"config.txt": "AKIA" + "ABCDEFGHIJKLMNOP\n"})
        with self.assertRaises(supply_chain.PolicyError):
            self.scan({"deploy/server.pem": "placeholder\n"})


if __name__ == "__main__":
    unittest.main()
