#!/usr/bin/env python3
"""Exercise the local Trino -> Polaris -> SeaweedFS path through a port-forward."""
from __future__ import annotations

import argparse
import json
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from typing import Any


class SmokeError(RuntimeError):
    pass


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise SmokeError("the local SQL endpoint must not redirect requests")


class TrinoClient:
    def __init__(self, url: str, timeout: float = 120) -> None:
        parsed = urllib.parse.urlsplit(url)
        if (
            parsed.scheme != "http"
            or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
            or timeout <= 0
        ):
            raise SmokeError("use a loopback HTTP port-forward URL and a positive timeout")
        self.url = url.rstrip("/")
        self.origin = (parsed.scheme, parsed.netloc)
        self.timeout = timeout
        self.opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}), NoRedirect()
        )

    def query(self, sql: str) -> dict[str, Any]:
        deadline = time.monotonic() + self.timeout
        next_uri: str | None = self.url + "/v1/statement"
        rows: list[list[Any]] = []
        columns: list[str] = []
        query_id = ""
        first = True
        while next_uri:
            target = urllib.parse.urlsplit(next_uri)
            if (
                (target.scheme, target.netloc) != self.origin
                or target.username is not None
                or target.password is not None
                or not target.path.startswith("/v1/statement")
            ):
                raise SmokeError("Trino returned a polling URL outside the local statement endpoint")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise SmokeError(f"query timed out: {query_id}")
            request = urllib.request.Request(
                next_uri,
                data=sql.encode("utf-8") if first else None,
                headers={"X-Trino-User": "shirokuma-local-smoke"},
                method="POST" if first else "GET",
            )
            try:
                with self.opener.open(request, timeout=min(remaining, 30)) as response:
                    raw = response.read(10 * 1024 * 1024 + 1)
                    if len(raw) > 10 * 1024 * 1024:
                        raise SmokeError("SQL response exceeds the smoke-test size limit")
                    page = json.loads(raw)
            except (urllib.error.URLError, TimeoutError, ValueError) as error:
                raise SmokeError(f"local Trino request failed: {error}") from error
            query_id = page.get("id", query_id)
            if "error" in page:
                error = page["error"]
                raise SmokeError(
                    f"{error.get('errorName', 'QUERY_FAILED')}: {error.get('message', '')}"
                )
            if "columns" in page:
                columns = [column["name"] for column in page["columns"]]
            rows.extend(page.get("data", []))
            next_uri = page.get("nextUri")
            first = False
        return {"query_id": query_id, "columns": columns, "rows": rows}


def smoke(client: TrinoClient, write_test: bool = False) -> dict[str, Any]:
    version = client.query("SELECT version() AS version")
    fixture = client.query("SELECT id, name FROM iceberg.smoke.fixture_v1 ORDER BY id")
    expected = [[1, "shirokuma"], [2, "polaris"]]
    if fixture["rows"] != expected:
        raise SmokeError(f"retained Iceberg fixture differs: {fixture['rows']!r}")
    result: dict[str, Any] = {
        "result": "passed",
        "scope": "local-experiment-only",
        "version": version["rows"],
        "fixture": fixture,
        "write_test": "not_requested",
    }
    if write_test:
        table = "iceberg.local_experiment.smoke_" + uuid.uuid4().hex
        client.query("CREATE SCHEMA IF NOT EXISTS iceberg.local_experiment")
        created = False
        primary_error: SmokeError | None = None
        try:
            client.query(
                f"CREATE TABLE {table} WITH (format = 'PARQUET') AS "
                "SELECT * FROM (VALUES (BIGINT '1', 'local'), (BIGINT '2', 'experiment')) "
                "AS fixture(id, name)"
            )
            created = True
            written = client.query(f"SELECT id, name FROM {table} ORDER BY id")
            if written["rows"] != [[1, "local"], [2, "experiment"]]:
                raise SmokeError("new experiment table did not round-trip the expected rows")
            result["write_test"] = {"table": table, "readback": written}
        except SmokeError as error:
            primary_error = error
        finally:
            # Only delete the unique table this invocation successfully created.
            if created:
                try:
                    client.query(f"DROP TABLE {table}")
                except SmokeError as error:
                    detail = f"cleanup failed for newly created table {table}: {error}"
                    if primary_error is not None:
                        detail = f"{primary_error}; {detail}"
                    raise SmokeError(detail) from error
        if primary_error is not None:
            raise primary_error
        result["write_test"]["table_removed"] = True
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:18080")
    parser.add_argument("--timeout", type=float, default=120)
    parser.add_argument("--write-test", action="store_true")
    args = parser.parse_args()
    try:
        result = smoke(TrinoClient(args.url, args.timeout), args.write_test)
    except SmokeError as error:
        print(json.dumps({"result": "failed", "error": str(error)}, ensure_ascii=False))
        return 1
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
