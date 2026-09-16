from __future__ import annotations

import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools" / "local_experiment"))
import local_experiment as experiment  # noqa: E402


def configmap():
    return {"apiVersion": "v1", "kind": "ConfigMap", "metadata": {
        "name": "repository-reconciliation-smoke", "namespace": "shirokuma-dev",
        "uid": "resource-1", "resourceVersion": "1"}, "data": {"source": "git"}}


def reconciler(name, suspended=None):
    return {"metadata": {"name": name, "uid": name + "-uid", "resourceVersion": "2"},
            "spec": {"suspend": suspended}}


class RunnerTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.directory = Path(self.temporary.name)
        self.addCleanup(self.temporary.cleanup)
        self.obj = configmap()
        payload = experiment.resource_list([self.obj])
        for name in ("rendered.json", "before.json"):
            (self.directory / name).write_text(payload)
        self.plan = {"version": 1, "context": experiment.CONTEXT, "component": "dev",
                     "rendered_sha256": experiment.digest(payload), "before_sha256": experiment.digest(payload),
                     "status": "prepared", "suspensions": []}
        experiment.write_json(self.directory / "plan.json", self.plan)
        self.flux = {"flux-system": reconciler("flux-system", True),
                     "shirokuma-dev": reconciler("shirokuma-dev", False)}

    def get(self, kind, name, namespace):
        if kind == experiment.FLUX_KIND:
            return copy.deepcopy(self.flux[name])
        return copy.deepcopy(self.obj)

    def test_fixed_context_used_for_every_kubectl_call(self):
        with mock.patch.object(experiment, "run", return_value="") as run:
            experiment.kube("get", "nodes")
        self.assertEqual(run.call_args.args[0][:4],
                         ["kubectl", "--context", "colima-mac-studio-solo", "--request-timeout=60s"])

    def test_remote_api_rejected_before_any_cluster_query(self):
        response = {"clusters": [{"cluster": {"server": "https://production.example:6443"}}]}
        with mock.patch.object(experiment, "kube", return_value=json.dumps(response)) as kube:
            with self.assertRaisesRegex(experiment.ExperimentError, "non-loopback"):
                experiment.check_context()
        self.assertEqual(kube.call_count, 1)

    def test_local_single_arm64_k3s_is_accepted(self):
        responses = [json.dumps({"clusters": [{"cluster": {"server": "https://127.0.0.1:6443"}}]}),
                     json.dumps({"items": [{"status": {"nodeInfo": {"architecture": "arm64", "kubeletVersion": "v1.35.0+k3s1"}}}]})]
        with mock.patch.object(experiment, "kube", side_effect=responses):
            experiment.check_context()

    def test_secret_or_extra_resource_cannot_be_applied(self):
        obj = configmap()
        obj["kind"] = "Secret"
        with self.assertRaisesRegex(experiment.ExperimentError, "allowlist"):
            experiment.validate_objects("dev", [obj])
        with self.assertRaisesRegex(experiment.ExperimentError, "allowlist"):
            experiment.validate_objects("dev", [configmap(), configmap()])

    def test_tampered_payload_rejected(self):
        (self.directory / "rendered.json").write_text("{}")
        with self.assertRaisesRegex(experiment.ExperimentError, "changed after prepare"):
            experiment.load_plan(self.directory)

    def test_changed_live_resource_refuses_before_suspending(self):
        self.obj["data"]["source"] = "other-edit"
        with mock.patch.object(experiment, "check_context"), mock.patch.object(experiment, "get", side_effect=self.get), mock.patch.object(experiment, "kube") as kube:
            with self.assertRaisesRegex(experiment.ExperimentError, "changed after prepare"):
                experiment.apply(self.directory)
        kube.assert_not_called()
        self.assertEqual(experiment.load_plan(self.directory)["status"], "prepared")

    def test_apply_preserves_existing_root_suspension_and_accepts_status_churn(self):
        self.obj["metadata"]["resourceVersion"] = "3"
        self.obj["status"] = {"arbitrary": "controller-status"}
        with mock.patch.object(experiment, "check_context"), mock.patch.object(experiment, "get", side_effect=self.get), mock.patch.object(experiment, "kube", side_effect=["", json.dumps(self.obj), ""]) as kube:
            experiment.apply(self.directory)
        calls = [call.args for call in kube.call_args_list]
        self.assertEqual(calls[0][4], "shirokuma-dev")
        self.assertEqual(calls[1], ("apply", "--dry-run=server", "-f", "-", "-o", "json"))
        operations = json.loads(calls[2][-1])
        self.assertEqual(operations[:2], [
            {"op": "test", "path": "/metadata/uid", "value": "resource-1"},
            {"op": "test", "path": "/metadata/resourceVersion", "value": "3"}])
        self.assertEqual(calls[2][2], "patch")
        plan = experiment.load_plan(self.directory)
        self.assertEqual(plan["status"], "applied")
        self.assertEqual([entry["changed"] for entry in plan["suspensions"]], [False, True])

    def test_failed_apply_keeps_recovery_plan_and_suspension_intent(self):
        with mock.patch.object(experiment, "check_context"), mock.patch.object(experiment, "get", side_effect=self.get), mock.patch.object(experiment, "kube", side_effect=["", experiment.ExperimentError("admission denied")]):
            with self.assertRaisesRegex(experiment.ExperimentError, "admission denied"):
                experiment.apply(self.directory)
        plan = experiment.load_plan(self.directory)
        self.assertEqual(plan["status"], "apply-failed")
        self.assertTrue(plan["suspensions"][1]["changed"])

    def test_resume_restores_only_changed_flags(self):
        self.flux["shirokuma-dev"]["spec"]["suspend"] = True
        self.plan.update(status="applied", suspensions=[
            {"name": "flux-system", "uid": "flux-system-uid", "previous": True, "changed": False},
            {"name": "shirokuma-dev", "uid": "shirokuma-dev-uid", "previous": None, "changed": True}])
        experiment.write_json(self.directory / "plan.json", self.plan)
        with mock.patch.object(experiment, "check_context"), mock.patch.object(experiment, "get", side_effect=self.get), mock.patch.object(experiment, "kube") as kube:
            experiment.resume(self.directory)
        self.assertEqual(kube.call_count, 1)
        self.assertEqual(kube.call_args.args[4], "shirokuma-dev")
        self.assertIsNone(json.loads(kube.call_args.args[-1])["spec"]["suspend"])
        self.assertEqual(experiment.load_plan(self.directory)["status"], "resumed")

    def test_recreated_flux_resource_refuses_resume(self):
        entry = {"name": "shirokuma-dev", "uid": "old-uid"}
        with mock.patch.object(experiment, "get", side_effect=self.get), mock.patch.object(experiment, "kube") as kube:
            with self.assertRaisesRegex(experiment.ExperimentError, "recreated"):
                experiment.set_suspended(entry, False, True)
        kube.assert_not_called()

    def test_remote_kustomize_input_is_rejected(self):
        (self.directory / "kustomization.yaml").write_text("ignored by decoder mock")
        with mock.patch.object(experiment, "ROOT", self.directory), mock.patch.object(experiment, "decode_yaml", return_value=[{"resources": ["https://example.com/manifests"]}]):
            with self.assertRaisesRegex(experiment.ExperimentError, "local checkout"):
                experiment.check_local_inputs(self.directory)

    def test_concurrent_resource_update_before_dry_run_stops_without_write(self):
        merged = copy.deepcopy(self.obj)
        merged["metadata"]["resourceVersion"] = "concurrent-2"
        with mock.patch.object(experiment, "get", side_effect=self.get), mock.patch.object(experiment, "kube", return_value=json.dumps(merged)) as kube:
            with self.assertRaisesRegex(experiment.ExperimentError, "during apply dry-run"):
                experiment.patch_existing(self.obj, self.obj)
        self.assertEqual(kube.call_count, 1)
        self.assertIn("--dry-run=server", kube.call_args.args)

    def test_atomic_patch_conflict_is_not_retried_as_creation(self):
        with mock.patch.object(experiment, "get", side_effect=self.get), mock.patch.object(experiment, "kube", side_effect=[json.dumps(self.obj), experiment.ExperimentError("UID/RV conflict or object deleted")]) as kube:
            with self.assertRaisesRegex(experiment.ExperimentError, "conflict"):
                experiment.patch_existing(self.obj, self.obj)
        self.assertEqual(kube.call_count, 2)
        self.assertEqual(kube.call_args.args[2], "patch")

    def test_concurrent_flux_suspend_change_is_not_overwritten(self):
        entry = {"name": "shirokuma-dev", "uid": "shirokuma-dev-uid"}
        self.flux["shirokuma-dev"]["spec"]["suspend"] = True
        with mock.patch.object(experiment, "get", side_effect=self.get), mock.patch.object(experiment, "kube") as kube:
            with self.assertRaisesRegex(experiment.ExperimentError, "concurrently"):
                experiment.set_suspended(entry, True, False)
        kube.assert_not_called()

    def test_unconfirmed_suspension_is_not_automatically_restored(self):
        with mock.patch.object(experiment, "check_context"), mock.patch.object(experiment, "get", side_effect=self.get), mock.patch.object(experiment, "kube", side_effect=experiment.ExperimentError("request timeout")):
            with self.assertRaisesRegex(experiment.ExperimentError, "timeout"):
                experiment.apply(self.directory)
        plan = experiment.load_plan(self.directory)
        self.assertFalse(plan["suspensions"][1]["changed"])
        self.assertTrue(plan["suspensions"][1]["pending"])
        with mock.patch.object(experiment, "check_context"), mock.patch.object(experiment, "kube") as kube:
            with self.assertRaisesRegex(experiment.ExperimentError, "ambiguous suspension"):
                experiment.resume(self.directory)
        kube.assert_not_called()

    def test_same_component_iteration_preserves_original_suspension_flags(self):
        inherited = {"suspensions": [
            {"name": "flux-system", "uid": "flux-system-uid", "previous": True, "changed": False},
            {"name": "shirokuma-dev", "uid": "shirokuma-dev-uid", "previous": False, "changed": True}]}
        self.flux["shirokuma-dev"]["spec"]["suspend"] = True
        with mock.patch.object(experiment, "check_context"), mock.patch.object(experiment, "get", side_effect=self.get), mock.patch.object(experiment, "kube", side_effect=[json.dumps(self.obj), ""]) as kube:
            experiment.apply(self.directory, inherited)
        self.assertEqual(kube.call_count, 2)  # Dry-run and atomic resource patch only.
        plan = experiment.load_plan(self.directory)
        self.assertFalse(plan["suspensions"][1]["previous"])
        self.assertTrue(plan["suspensions"][1]["changed"])

    def test_crash_before_inheritance_keeps_previous_recovery_pointer(self):
        marker = self.directory / "active.json"
        previous = self.directory / "previous-run"
        # This is the exact durable state at the reservation/inheritance crash
        # boundary: a prepared new run has no recovery flags of its own yet.
        experiment.write_json(marker, {"run": str(self.directory), "previous_run": str(previous)})
        active = experiment.active_run(marker)
        self.assertEqual(active, {"run": str(previous)})
        self.assertEqual(json.loads(marker.read_text()), active)
        self.assertEqual(experiment.load_plan(self.directory)["suspensions"], [])

    def test_inherited_recovery_pointer_stays_on_run_after_mutations_begin(self):
        marker = self.directory / "active.json"
        self.plan["status"] = "applying"
        experiment.write_json(self.directory / "plan.json", self.plan)
        reservation = {"run": str(self.directory), "previous_run": str(self.directory / "previous-run")}
        experiment.write_json(marker, reservation)
        self.assertEqual(experiment.active_run(marker), reservation)


if __name__ == "__main__":
    unittest.main()
