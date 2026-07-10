from __future__ import annotations

import os
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/colima_baseline.sh"
BASELINE_STATUS_JSON = (
    '{"display_name":"colima [profile=mac-studio-solo]",'
    '"driver":"macOS Virtualization.Framework","arch":"aarch64",'
    '"runtime":"docker","kubernetes":true,"cpu":16,'
    '"memory":103079215104,"disk":429496729600}'
)


class ColimaBaselineAutomationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.temp = Path(self.tempdir.name)
        self.log = self.temp / "commands.log"
        fake = self.temp / "fake-tool"
        fake.write_text(
            textwrap.dedent(
                """\
                #!/bin/sh
                printf '%s %s\\n' "$(basename "$0")" "$*" >> "$FAKE_COMMAND_LOG"
                case "$(basename "$0") $*" in
                  "colima status --profile mac-studio-solo --json")
                    printf '%s\\n' "$FAKE_STATUS_JSON"
                    ;;
                  "colima ssh --profile mac-studio-solo -- uname -m")
                    printf '%s\\n' aarch64
                    ;;
                  *"binfmt_misc"*)
                    [ -z "${FAKE_FOREIGN_BINFMT:-}" ] || printf '%s\\n' "$FAKE_FOREIGN_BINFMT"
                    ;;
                  *"findmnt -n -o SOURCE /var/lib/docker"*)
                    printf '%s\\n' "${FAKE_DATA_DISK_BYTES:-429496729600}"
                    ;;
                  "kubectl config current-context"*)
                    [ -z "${FAKE_CURRENT_CONTEXT:-}" ] || printf '%s\\n' "$FAKE_CURRENT_CONTEXT"
                    ;;
                  "docker context show"*)
                    [ -z "${FAKE_DOCKER_CONTEXT:-}" ] || printf '%s\\n' "$FAKE_DOCKER_CONTEXT"
                    ;;
                  *"status.nodeInfo.architecture"*)
                    printf 'shirokuma=%s\\n' "${FAKE_NODE_ARCH:-arm64}"
                    ;;
                  *"Ready"*) printf 'shirokuma=True\\n' ;;
                  *) : ;;
                esac
                """
            ),
            encoding="utf-8",
        )
        fake.chmod(0o755)
        for name in ("colima", "kubectl", "helm", "docker"):
            (self.temp / name).symlink_to(fake)

    def run_script(
        self,
        *args: str,
        arch: str = "arm64",
        current_context: str = "",
        current_docker_context: str = "",
        status_json: str = BASELINE_STATUS_JSON,
        foreign_binfmt: str = "",
        data_disk_bytes: int = 429496729600,
    ) -> subprocess.CompletedProcess[str]:
        env = os.environ.copy()
        env.update(
            {
                "COLIMA_BIN": str(self.temp / "colima"),
                "KUBECTL_BIN": str(self.temp / "kubectl"),
                "HELM_BIN": str(self.temp / "helm"),
                "DOCKER_BIN": str(self.temp / "docker"),
                "FAKE_COMMAND_LOG": str(self.log),
                "FAKE_NODE_ARCH": arch,
                "FAKE_CURRENT_CONTEXT": current_context,
                "FAKE_DOCKER_CONTEXT": current_docker_context,
                "FAKE_STATUS_JSON": status_json,
                "FAKE_FOREIGN_BINFMT": foreign_binfmt,
                "FAKE_DATA_DISK_BYTES": str(data_disk_bytes),
            }
        )
        return subprocess.run(
            [str(SCRIPT), *args],
            cwd=ROOT,
            env=env,
            check=False,
            capture_output=True,
            text=True,
        )

    def test_status_checks_the_authoritative_context_and_helm_access(self) -> None:
        result = self.run_script("status")

        self.assertEqual(result.returncode, 0, result.stderr)
        commands = self.log.read_text(encoding="utf-8")
        self.assertIn("kubectl --context colima-mac-studio-solo cluster-info", commands)
        self.assertIn("kubectl --context colima-mac-studio-solo get nodes", commands)
        self.assertIn(
            "helm list --kube-context colima-mac-studio-solo --all-namespaces",
            commands,
        )

    def test_status_fails_closed_before_helm_when_node_is_not_arm64(self) -> None:
        result = self.run_script("status", arch="amd64")

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("expected arm64", result.stderr)
        commands = self.log.read_text(encoding="utf-8")
        self.assertNotIn("helm list", commands)

    def test_start_uses_the_pinned_solo_lite_profile(self) -> None:
        result = self.run_script(
            "start",
            current_context="another-cluster",
            current_docker_context="another-docker",
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        commands = self.log.read_text(encoding="utf-8")
        self.assertIn(
            "colima start --profile mac-studio-solo --vm-type=vz --arch aarch64 "
            "--cpu 16 --memory 96 --disk 400 --kubernetes --runtime docker "
            "--binfmt=false --activate=false",
            commands,
        )
        self.assertIn("kubectl config use-context another-cluster", commands)
        self.assertIn("docker context use another-docker", commands)

    def test_status_rejects_each_non_baseline_profile_field(self) -> None:
        mismatches = {
            "driver": BASELINE_STATUS_JSON.replace(
                '"driver":"macOS Virtualization.Framework"', '"driver":"qemu"'
            ),
            "arch": BASELINE_STATUS_JSON.replace('"arch":"aarch64"', '"arch":"x86_64"'),
            "runtime": BASELINE_STATUS_JSON.replace(
                '"runtime":"docker"', '"runtime":"containerd"'
            ),
            "kubernetes": BASELINE_STATUS_JSON.replace(
                '"kubernetes":true', '"kubernetes":false'
            ),
            "cpu": BASELINE_STATUS_JSON.replace('"cpu":16', '"cpu":160'),
            "memory": BASELINE_STATUS_JSON.replace(
                '"memory":103079215104', '"memory":1030792151040'
            ),
            "disk": BASELINE_STATUS_JSON.replace(
                '"disk":429496729600', '"disk":4294967296000'
            ),
        }

        for field, status_json in mismatches.items():
            with self.subTest(field=field):
                result = self.run_script("status", status_json=status_json)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(f"expected {field}=", result.stderr)
                commands = self.log.read_text(encoding="utf-8")
                self.assertNotIn("kubectl --context", commands)
                self.assertNotIn("helm list", commands)
                self.log.unlink()

    def test_status_rejects_foreign_architecture_binfmt_before_kubernetes(self) -> None:
        result = self.run_script("status", foreign_binfmt="qemu-x86_64")

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("foreign architecture emulation is enabled", result.stderr)
        commands = self.log.read_text(encoding="utf-8")
        self.assertNotIn("kubectl --context", commands)
        self.assertNotIn("helm list", commands)

    def test_status_rejects_oversized_backing_data_disk(self) -> None:
        result = self.run_script("status", data_disk_bytes=1099511627776)

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Colima data disk does not match baseline", result.stderr)
        commands = self.log.read_text(encoding="utf-8")
        self.assertNotIn("kubectl --context", commands)
        self.assertNotIn("helm list", commands)

    def test_reset_requires_explicit_data_loss_confirmation(self) -> None:
        result = self.run_script("reset")

        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.log.exists(), "reset must not invoke Colima without confirmation")

    def test_reset_force_stops_before_destructive_delete(self) -> None:
        result = self.run_script("reset", "--confirm-data-loss")

        self.assertEqual(result.returncode, 0, result.stderr)
        commands = self.log.read_text(encoding="utf-8")
        stop = "colima stop --profile mac-studio-solo --force"
        delete = "colima delete --profile mac-studio-solo --data --force"
        self.assertIn(stop, commands)
        self.assertIn(delete, commands)
        self.assertLess(commands.index(stop), commands.index(delete))


if __name__ == "__main__":
    unittest.main()
