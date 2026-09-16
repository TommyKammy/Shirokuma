from __future__ import annotations
import json
import contextlib
import io
import sys
import tempfile
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools/local_experiment"))
from metadata import ingest, runtime
from metadata.client import Client, NoRedirect


class MetadataTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.token = "a" * 32
        self.evidence = self.root / "tmp/local-experiment/dataops" / self.token
        (self.evidence / "first/build-target").mkdir(parents=True)
        self.summary = {"result": "passed", "run_token": self.token, "fixture_unchanged": True,
                        "query_drain": {"verified": True}, "attempts": [{"success": True,
                        "label": "first", "run_id": "11111111-1111-4111-8111-111111111111"}]}
        source_id = "source.shirokuma_local_dataops.retained_smoke.fixture_v1"
        models = {}
        for name, suffix, dependency in [("stg_fixture", "stg", source_id),
                ("mart_fixture", "mart", "model.shirokuma_local_dataops.stg_fixture")]:
            models["model.shirokuma_local_dataops." + name] = {"resource_type": "model", "name": name,
                "database": "iceberg", "schema": "local_experiment", "alias": f"dataops_{self.token}_{suffix}",
                "depends_on": {"nodes": [dependency]}, "columns": {"id": {"name": "id"}}}
        self.manifest = {"nodes": models, "sources": {source_id: {"name": "fixture_v1",
                         "database": "iceberg", "schema": "smoke", "identifier": "fixture_v1"}}}
        self.results = {"metadata": {"generated_at": "2026-09-16T05:12:03Z"},
                        "results": [{"unique_id": key, "status": "success", "timing": [{"name": "execute",
                          "started_at": "2026-09-16T05:12:01Z", "completed_at": "2026-09-16T05:12:02Z"}]} for key in models]}
        self.write_evidence()

    def write_evidence(self):
        for path, data in [("summary.json", self.summary), ("first/build-target/manifest.json", self.manifest),
                           ("first/build-target/run_results.json", self.results)]:
            (self.evidence / path).write_text(json.dumps(data))

    def read(self):
        with patch.object(ingest, "ROOT", self.root):
            return ingest.read_evidence(self.evidence)

    def test_rejects_failed_or_undrained_evidence(self):
        self.summary["query_drain"]["verified"] = False
        self.write_evidence()
        with self.assertRaisesRegex(ValueError, "query drain"):
            self.read()

    def test_rejects_success_summary_without_matching_dbt_success(self):
        self.results["results"][0]["status"] = "error"
        self.write_evidence()
        with self.assertRaisesRegex(ValueError, "both models"):
            self.read()

    def test_rejects_scope_changes_in_generated_table_name(self):
        next(iter(self.manifest["nodes"].values()))["schema"] = "retained_data"
        self.write_evidence()
        with self.assertRaisesRegex(ValueError, "table identity"):
            self.read()

    def test_openlineage_events_retain_historical_run_and_dependency_chain(self):
        summary, attempt, manifest, results, models, hashes = self.read()
        events = ingest.events_for(summary, attempt, manifest, results, models)
        self.assertEqual(events, ingest.events_for(summary, attempt, manifest, results, models))
        self.assertEqual(len(hashes), 3)
        actual = {(e["inputs"][0]["name"], e["outputs"][0]["name"]) for e in events}
        stage = f"iceberg.local_experiment.dataops_{self.token}_stg"
        mart = f"iceberg.local_experiment.dataops_{self.token}_mart"
        self.assertEqual(actual, {("iceberg.smoke.fixture_v1", stage), (stage, mart)})
        self.assertEqual([e["eventType"] for e in events], ["START", "COMPLETE"] * 2)
        for event in events:
            self.assertEqual(event["eventTime"], "2026-09-16T05:12:01Z" if event["eventType"] == "START" else "2026-09-16T05:12:02Z")
            self.assertTrue(event["schemaURL"].endswith("#/$defs/RunEvent"))
            self.assertEqual(event["run"]["facets"]["parent"]["run"]["runId"], attempt["run_id"])

    def test_private_scoped_compose_has_only_loopback_frontend(self):
        config = json.loads((ROOT / "deploy/local-experiment/metadata/compose.json").read_text())
        for name, service in config["services"].items():
            self.assertEqual(service["platform"], "linux/arm64")
            self.assertIn("@sha256:", service["image"])
            self.assertEqual(service.get("ports", []), ["127.0.0.1:18585:8585"] if name == "server" else [])
            self.assertNotIn("privileged", service)
        self.assertTrue(config["networks"]["metadata"]["internal"])
        self.assertNotIn("frontend", config["services"]["postgresql"]["networks"])
        self.assertNotIn("frontend", config["services"]["search"]["networks"])

    def test_remote_docker_context_is_rejected(self):
        remote = [{"Endpoints": {"docker": {"Host": "tcp://remote:2375"}}}]
        with patch.object(runtime, "docker", return_value=json.dumps(remote).encode()) as docker:
            with self.assertRaisesRegex(RuntimeError, "dedicated local"):
                runtime.check_runtime()
        self.assertEqual(docker.call_count, 1)

    def test_client_rejects_redirect_and_absolute_api_path(self):
        with self.assertRaisesRegex(RuntimeError, "redirect"):
            NoRedirect().redirect_request(None, None, 302, None, None, "https://remote")
        with self.assertRaises(ValueError):
            Client().request("GET", "https://remote")

    def test_fixture_mismatch_prevents_all_metadata_writes(self):
        client, trino = Mock(), Mock()
        trino.query.return_value = {"rows": [[99, "changed"]]}
        with patch.object(ingest, "ROOT", self.root):
            with self.assertRaisesRegex(ValueError, "retained fixture differs"):
                ingest.ingest(client, self.evidence, trino)
        client.request.assert_not_called()

    def status_rows(self):
        config = json.loads(runtime.COMPOSE.read_text())
        return [{"Service": name, "State": "running", "Health": "healthy",
                 "Image": config["services"][name]["image"],
                 "Labels": "private-host-paths-must-not-be-returned"}
                for name in ("server", "postgresql", "search")]

    def test_runtime_status_is_compact_and_excludes_finished_migration(self):
        rows = self.status_rows() + [{"Service": "migrate", "State": "exited", "ExitCode": 0}]
        (self.root / ".env").touch()
        with patch.object(runtime, "STATE", self.root), patch.object(runtime, "compose", return_value=(
                '\n'.join(json.dumps(row) for row in rows)).encode()):
            result = runtime.status()
        self.assertTrue(result["ready"])
        self.assertEqual(len(result["services"]), 3)
        self.assertTrue(all(set(service) == {"name", "state", "health", "image"}
                            for service in result["services"]))
        self.assertNotIn("private-host", json.dumps(result))

    def test_runtime_status_rejects_missing_unhealthy_and_wrong_image(self):
        (self.root / ".env").touch()
        for failure in ("missing", "unhealthy", "wrong-image", "ambiguous"):
            rows = self.status_rows()
            if failure == "missing":
                rows.pop()
            elif failure == "unhealthy":
                rows[0]["Health"] = "unhealthy"
            elif failure == "wrong-image":
                rows[0]["Image"] = "unrelated:latest"
            else:
                rows.append(rows[0])
            with patch.object(runtime, "STATE", self.root), patch.object(runtime, "compose", return_value=json.dumps(rows).encode()):
                self.assertFalse(runtime.status()["ready"], failure)

    def test_status_does_not_create_credentials_and_returns_nonzero_if_not_ready(self):
        with patch.object(runtime, "STATE", self.root), patch.object(runtime, "check_runtime"), \
                patch.object(runtime, "initialize") as initialize, \
                patch.object(runtime.sys, "argv", ["runtime.py", "status"]), \
                contextlib.redirect_stdout(io.StringIO()) as output:
            result = runtime.main()
        self.assertEqual(result, 1)
        self.assertFalse(json.loads(output.getvalue())["ready"])
        initialize.assert_not_called()

    def test_lost_environment_does_not_rotate_secrets_over_retained_resources(self):
        for resources in [(b"existing-container", b""), (b"", b"retained-volume")]:
            with patch.object(runtime, "STATE", self.root), \
                    patch.object(runtime, "docker", side_effect=resources), \
                    patch.object(runtime, "invoke") as invoke, \
                    patch.object(runtime.secrets, "token_urlsafe") as random:
                with self.assertRaisesRegex(RuntimeError, "refusing automatic secret rotation"):
                    runtime.initialize()
            invoke.assert_not_called()
            random.assert_not_called()
            self.assertFalse((self.root / ".env").exists())

    def test_local_socket_suffix_cannot_name_another_runtime(self):
        other = [{"Endpoints": {"docker": {"Host": "unix:///tmp/.colima/mac-studio-solo/docker.sock.other"}}}]
        with patch.object(runtime, "docker", return_value=json.dumps(other).encode()):
            with self.assertRaisesRegex(RuntimeError, "dedicated local"):
                runtime.check_runtime()


if __name__ == "__main__":
    unittest.main()
