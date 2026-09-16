from __future__ import annotations

import importlib.util
import io
import json
import unittest
from pathlib import Path
from unittest.mock import Mock


SPEC = importlib.util.spec_from_file_location(
    "local_trino_smoke", Path(__file__).resolve().parents[1] / "tools/local_experiment/local_trino_smoke.py"
)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class TrinoSmokeTests(unittest.TestCase):
    def client(self, pages):
        client = MODULE.TrinoClient("http://127.0.0.1:18080")
        client.opener = Mock()
        client.opener.open.side_effect = [
            io.BytesIO(json.dumps(page).encode()) for page in pages
        ]
        return client

    def test_collects_paginated_results_in_order(self):
        client = self.client([
            {"id": "q1", "nextUri": "http://127.0.0.1:18080/v1/statement/queued/q1"},
            {"id": "q1", "columns": [{"name": "id"}], "data": [[1]],
             "nextUri": "http://127.0.0.1:18080/v1/statement/executing/q1"},
            {"id": "q1", "data": [[2]]},
        ])
        self.assertEqual(client.query("SELECT id"), {
            "query_id": "q1", "columns": ["id"], "rows": [[1], [2]]
        })
        requests = [call.args[0] for call in client.opener.open.call_args_list]
        self.assertEqual([request.get_method() for request in requests], ["POST", "GET", "GET"])
        self.assertEqual(requests[0].data, b"SELECT id")

    def test_rejects_remote_endpoint_and_polling_redirect(self):
        with self.assertRaises(MODULE.SmokeError):
            MODULE.TrinoClient("http://example.com:8080")
        client = self.client([{"nextUri": "http://example.com/v1/statement/q1"}])
        with self.assertRaisesRegex(MODULE.SmokeError, "outside"):
            client.query("SELECT 1")
        self.assertEqual(client.opener.open.call_count, 1)

    def test_http_success_with_query_error_is_failure(self):
        client = self.client([{"error": {"errorName": "TABLE_NOT_FOUND", "message": "missing"}}])
        with self.assertRaisesRegex(MODULE.SmokeError, "TABLE_NOT_FOUND"):
            client.query("SELECT 1")

    def test_write_test_never_drops_table_when_creation_failed(self):
        client = Mock()
        client.query.side_effect = [
            {"rows": [["483"]]}, {"rows": [[1, "shirokuma"], [2, "polaris"]]},
            {"rows": []}, MODULE.SmokeError("CREATE failed"),
        ]
        with self.assertRaisesRegex(MODULE.SmokeError, "CREATE failed"):
            MODULE.smoke(client, write_test=True)
        self.assertFalse(any(call.args[0].startswith("DROP") for call in client.query.call_args_list))

    def test_write_test_cleans_only_new_table_even_after_read_failure(self):
        client = Mock()
        client.query.side_effect = [
            {"rows": [["483"]]}, {"rows": [[1, "shirokuma"], [2, "polaris"]]},
            {"rows": []}, {"rows": []}, MODULE.SmokeError("read failed"), {"rows": []},
        ]
        with self.assertRaisesRegex(MODULE.SmokeError, "read failed"):
            MODULE.smoke(client, write_test=True)
        create = client.query.call_args_list[3].args[0]
        drop = client.query.call_args_list[-1].args[0]
        table = create.split()[2]
        self.assertTrue(table.startswith("iceberg.local_experiment.smoke_"))
        self.assertEqual(drop, "DROP TABLE " + table)

    def test_cleanup_failure_preserves_original_error_and_owned_table(self):
        client = Mock()
        client.query.side_effect = [
            {"rows": [["483"]]}, {"rows": [[1, "shirokuma"], [2, "polaris"]]},
            {"rows": []}, {"rows": []}, MODULE.SmokeError("read failed"),
            MODULE.SmokeError("drop failed"),
        ]
        with self.assertRaises(MODULE.SmokeError) as caught:
            MODULE.smoke(client, write_test=True)
        message = str(caught.exception)
        self.assertIn("read failed", message)
        self.assertIn("drop failed", message)
        self.assertIn(client.query.call_args_list[3].args[0].split()[2], message)


if __name__ == "__main__":
    unittest.main()
