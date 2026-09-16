import copy
import importlib.util
import json
from pathlib import Path
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location("local_loki", Path(__file__).resolve().parents[1] / "tools/local_experiment/loki/manage.py")
LOKI = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(LOKI)


class LokiTests(unittest.TestCase):
    def record(self):
        return {"task_id": "local-task", "trace_id": "a" * 32, "span_id": "b" * 16,
                "action": "doctor", "status": "success"}

    def test_raw_payload_and_query_injection_are_rejected_before_network(self):
        with patch.object(LOKI, "request") as request:
            for record in [dict(self.record(), prompt="private"), dict(self.record(), task_id='x" |~ ".*'),
                           dict(self.record(), trace_id="bad"), dict(self.record(), status="unknown")]:
                with self.assertRaises(LOKI.LokiError):
                    LOKI.push_record(record)
            with self.assertRaises(LOKI.LokiError):
                LOKI.query_task('" | json')
            request.assert_not_called()

    def test_low_cardinality_labels_and_bounded_correlated_payload(self):
        with patch.object(LOKI, "request") as request:
            LOKI.push_record(self.record())
            payload = request.call_args.args[1]["streams"][0]
            self.assertEqual(set(payload["stream"]), {"service_name", "component"})
            self.assertEqual(json.loads(payload["values"][0][1]), self.record())

    def test_remote_docker_endpoint_refused(self):
        value = [{"Endpoints": {"docker": {"Host": "tcp://remote:2376"}}}]
        with patch.object(LOKI, "check_kubernetes_context"), patch.object(LOKI, "docker", return_value=json.dumps(value)):
            with self.assertRaises(LOKI.LokiError):
                LOKI.check_context()

    def test_unowned_mount_or_exposure_refused(self):
        original = {"Config": {"Image": LOKI.IMAGE, "Labels": {LOKI.LABEL: "loki-local"}},
                    "HostConfig": {"PortBindings": {"3100/tcp": [{"HostIp": "127.0.0.1", "HostPort": "13100"}]},
                                   "NanoCpus": 10**9, "Memory": 1024**3},
                    "Mounts": [{"Type": "volume", "Name": LOKI.VOLUME, "Destination": "/loki", "RW": True},
                               {"Type": "bind", "Source": str(LOKI.HERE / "config.yaml"),
                                "Destination": "/etc/loki/local-config.yaml", "RW": False}]}
        for drift in ("mount", "exposure", "image"):
            value = copy.deepcopy(original)
            if drift == "mount":
                value["Mounts"][0]["Name"] = "retained-unrelated-data"
            elif drift == "exposure":
                value["HostConfig"]["PortBindings"]["3100/tcp"][0]["HostIp"] = "0.0.0.0"
            else:
                value["Config"]["Image"] = "unrelated:latest"
            with patch.object(LOKI, "docker", side_effect=["owned-id", json.dumps([value])]):
                with self.assertRaises(LOKI.LokiError):
                    LOKI.container()

    def test_redirect_refused(self):
        with self.assertRaises(LOKI.LokiError):
            LOKI.NoRedirect().redirect_request(None, None, 302, "", {}, "https://external.invalid")

    def test_slow_readiness_request_cannot_multiply_total_timeout(self):
        with patch.object(LOKI.time, "monotonic", side_effect=[100, 100, 103]), \
                patch.object(LOKI.time, "sleep") as sleep, \
                patch.object(LOKI, "request", side_effect=TimeoutError()) as request:
            with self.assertRaisesRegex(LOKI.LokiError, "timed out"):
                LOKI.wait_ready(timeout=3)
        request.assert_called_once_with("/ready", timeout=3)
        sleep.assert_not_called()

    def test_successful_readiness_returns_without_extra_poll(self):
        with patch.object(LOKI.time, "monotonic", side_effect=[100, 100]), \
                patch.object(LOKI, "request", return_value="ready") as request:
            LOKI.wait_ready()
        request.assert_called_once_with("/ready", timeout=10)


if __name__ == "__main__":
    unittest.main()
