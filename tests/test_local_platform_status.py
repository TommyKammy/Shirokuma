"""Readiness must describe observed service health, not only a running process."""
import importlib.util
import json
from pathlib import Path
import subprocess
import unittest
from unittest.mock import Mock, patch
import urllib.error


SPEC = importlib.util.spec_from_file_location(
    "local_platform_status", Path(__file__).resolve().parents[1]
    / "tools/local_experiment/platform_status.py")
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class PlatformStatusTests(unittest.TestCase):
    def check(self, name, value, code=0):
        result = subprocess.CompletedProcess([], code, stdout=json.dumps(value), stderr="private backend error")
        with patch.object(MODULE.subprocess, "run", return_value=result):
            return MODULE.check((name, MODULE.COMMANDS[name]))[1]

    def test_running_superset_requires_successful_health_probe(self):
        with patch.object(MODULE, "superset_health", return_value=MODULE.observation("not-ready")) as health:
            self.assertEqual(self.check("superset", {"running": True}),
                             {"ready": False, "status": "not-ready"})
            health.assert_called_once_with()

    def test_stopped_superset_does_not_probe_other_listener(self):
        with patch.object(MODULE, "superset_health") as health:
            self.assertEqual(self.check("superset", {"running": False}),
                             {"ready": False, "status": "not-ready"})
            health.assert_not_called()

    def test_fixed_health_probe_disables_proxy_and_redirects(self):
        response = Mock(status=200)
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        response.read.return_value = b"OK"
        opener = Mock()
        opener.open.return_value = response
        with patch.object(MODULE.urllib.request, "build_opener", return_value=opener) as build:
            self.assertEqual(MODULE.superset_health(), {"ready": True, "status": "ready"})
        proxy, redirect = build.call_args.args
        self.assertEqual(proxy.proxies, {})
        self.assertIsInstance(redirect, MODULE.SUPERSET.NoRedirect)
        opener.open.assert_called_once_with("http://127.0.0.1:18088/health", timeout=3)
        response.read.assert_called_once_with(17)

    def test_http_200_with_wrong_or_oversized_body_is_not_ready(self):
        for body in (b"not okay", b"", b"OK" + b" " * 15):
            with self.subTest(body=body):
                response = Mock(status=200)
                response.__enter__ = Mock(return_value=response)
                response.__exit__ = Mock(return_value=False)
                response.read.return_value = body
                opener = Mock()
                opener.open.return_value = response
                with patch.object(MODULE.urllib.request, "build_opener", return_value=opener):
                    result = MODULE.superset_health()
                self.assertEqual(result["status"], "not-ready")
                self.assertFalse(result["ready"])
                response.read.assert_called_once_with(17)

    def test_http_failure_and_redirect_are_observed_not_ready(self):
        errors = [urllib.error.HTTPError("private", 503, "private", {}, None),
                  MODULE.SUPERSET.LocalError("private redirect details")]
        for error in errors:
            with self.subTest(error=type(error).__name__):
                opener = Mock()
                opener.open.side_effect = error
                with patch.object(MODULE.urllib.request, "build_opener", return_value=opener):
                    result = MODULE.superset_health()
                self.assertFalse(result["ready"])
                self.assertEqual(result["status"], "not-ready")
                self.assertNotIn("private", json.dumps(result))

    def test_unavailable_health_is_unknown_without_backend_details(self):
        for error in (TimeoutError("private"), urllib.error.URLError("private")):
            opener = Mock()
            opener.open.side_effect = error
            with patch.object(MODULE.urllib.request, "build_opener", return_value=opener):
                result = MODULE.superset_health()
            self.assertEqual(result, {"ready": False, "status": "unknown",
                                      "reason": "health endpoint unavailable"})

    def test_known_positive_and_negative_component_reports(self):
        cases = [("lakehouse", {"status": "healthy"}, True),
                 ("lakehouse", {"status": "degraded"}, False),
                 ("metadata", {"ready": True}, True),
                 ("metadata", {"ready": False}, False),
                 ("loki", {"ready": True}, True),
                 ("loki", {"ready": False}, False),
                 ("agenthouse", {"result": "ready"}, True)]
        for name, value, ready in cases:
            with self.subTest(name=name, ready=ready):
                self.assertEqual(self.check(name, value, 0 if ready else 1),
                                 {"ready": ready, "status": "ready" if ready else "not-ready"})

    def test_missing_or_malformed_readiness_is_unknown(self):
        for name, value in [("superset", {"running": "true"}), ("metadata", {}),
                            ("loki", {"ready": 1}), ("lakehouse", {"status": "other"}),
                            ("agenthouse", {"result": "failed"}), ("metadata", [])]:
            with self.subTest(name=name, value=value):
                result = self.check(name, value)
                self.assertFalse(result["ready"])
                self.assertEqual(result["status"], "unknown")
                self.assertNotIn("private", json.dumps(result))

    def test_exit_failure_cannot_assert_ready_or_trigger_probe(self):
        with patch.object(MODULE, "superset_health") as health:
            for code in (1, 2):
                result = self.check("superset", {"running": True}, code)
                self.assertFalse(result["ready"])
                self.assertEqual(result["status"], "unknown")
            health.assert_not_called()

    def test_timeout_or_invalid_json_is_unknown(self):
        result = subprocess.CompletedProcess([], 0, stdout="private invalid JSON", stderr="private")
        for failure in (subprocess.TimeoutExpired("private", 60), result):
            with patch.object(MODULE.subprocess, "run", **(
                    {"side_effect": failure} if isinstance(failure, Exception) else {"return_value": failure})):
                result = MODULE.check(("metadata", MODULE.COMMANDS["metadata"]))[1]
            self.assertFalse(result["ready"])
            self.assertEqual(result["status"], "unknown")
            self.assertNotIn("private", json.dumps(result))


if __name__ == "__main__":
    unittest.main()
