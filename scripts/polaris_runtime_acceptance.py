#!/usr/bin/env python3
"""Run credential-safe live acceptance for the local-lite Polaris runtime."""

from __future__ import annotations

import argparse
import base64
import datetime as dt
import hashlib
import http.client
import json
import os
import platform
import re
import shutil
import socket
import stat
import subprocess
import sys
import tempfile
import time
import urllib.parse
from pathlib import Path
from typing import Any, Mapping, Sequence


DEFAULT_CONTEXT = "colima-mac-studio-solo"
DEFAULT_NAMESPACE = "shirokuma-dev"
DEFAULT_BACKUP_ROOT = Path.home() / "Backups" / "Shirokuma" / "polaris"
REQUIRED_KUSTOMIZATIONS = (
    "shirokuma-object-storage",
    "shirokuma-catalog-database",
    "shirokuma-catalog-bootstrap",
    "shirokuma-catalog",
)
CATALOG_NAME = re.compile(r"^[a-z][a-z0-9_]{0,62}$")
DATABASE_NAME = re.compile(r"^[a-z][a-z0-9_]{0,62}$")
GIT_SHA = re.compile(r"^[0-9a-f]{40}$")


class AcceptanceError(RuntimeError):
    """Safe-to-print acceptance failure."""


def _run(
    command: Sequence[str],
    *,
    input_bytes: bytes | None = None,
    timeout: int = 120,
) -> subprocess.CompletedProcess[bytes]:
    try:
        return subprocess.run(
            list(command),
            input=input_bytes,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=True,
            timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise AcceptanceError(f"command failed: {command[0]}") from error


def _run_json(command: Sequence[str]) -> dict[str, Any]:
    result = _run(command)
    try:
        value = json.loads(result.stdout)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise AcceptanceError(f"command returned invalid JSON: {command[0]}") from error
    if not isinstance(value, dict):
        raise AcceptanceError(f"command returned a non-object JSON value: {command[0]}")
    return value


def _kubectl(context: str, *arguments: str) -> list[str]:
    return ["kubectl", "--context", context, *arguments]


def _ready_condition(resource: Mapping[str, Any]) -> Mapping[str, Any] | None:
    status = resource.get("status")
    if not isinstance(status, Mapping):
        return None
    conditions = status.get("conditions")
    if not isinstance(conditions, list):
        return None
    return next(
        (
            condition
            for condition in conditions
            if isinstance(condition, Mapping) and condition.get("type") == "Ready"
        ),
        None,
    )


def _complete_condition(resource: Mapping[str, Any]) -> Mapping[str, Any] | None:
    status = resource.get("status")
    if not isinstance(status, Mapping):
        return None
    conditions = status.get("conditions")
    if not isinstance(conditions, list):
        return None
    return next(
        (
            condition
            for condition in conditions
            if isinstance(condition, Mapping)
            and condition.get("type") == "Complete"
            and condition.get("status") == "True"
        ),
        None,
    )


def collect_readiness(context: str, namespace: str) -> dict[str, Any]:
    flux = _run_json(
        _kubectl(
            context,
            "get",
            "kustomizations.kustomize.toolkit.fluxcd.io",
            "-n",
            "flux-system",
            "-o",
            "json",
        )
    )
    items = flux.get("items")
    if not isinstance(items, list):
        raise AcceptanceError("Flux Kustomization inventory is missing")
    by_name = {
        item.get("metadata", {}).get("name"): item
        for item in items
        if isinstance(item, Mapping)
    }
    kustomizations: list[dict[str, Any]] = []
    revisions: set[str] = set()
    for name in REQUIRED_KUSTOMIZATIONS:
        item = by_name.get(name)
        if not isinstance(item, Mapping):
            raise AcceptanceError(f"required Kustomization is absent: {name}")
        condition = _ready_condition(item)
        if not condition or condition.get("status") != "True":
            raise AcceptanceError(f"required Kustomization is not Ready: {name}")
        status = item.get("status")
        revision = status.get("lastAppliedRevision") if isinstance(status, Mapping) else None
        if not isinstance(revision, str) or "@sha1:" not in revision:
            raise AcceptanceError(f"required Kustomization has no applied revision: {name}")
        revisions.add(revision)
        kustomizations.append(
            {
                "name": name,
                "ready": True,
                "revision": revision,
                "reason": condition.get("reason"),
            }
        )
    if len(revisions) != 1:
        raise AcceptanceError("required Kustomizations are not on one revision")

    deployment = _run_json(
        _kubectl(context, "get", "deployment", "polaris", "-n", namespace, "-o", "json")
    )
    deployment_status = deployment.get("status")
    if not isinstance(deployment_status, Mapping) or deployment_status.get(
        "availableReplicas"
    ) != 1:
        raise AcceptanceError("Polaris Deployment is not available")

    statefulset = _run_json(
        _kubectl(
            context,
            "get",
            "statefulset",
            "polaris-postgresql",
            "-n",
            namespace,
            "-o",
            "json",
        )
    )
    statefulset_status = statefulset.get("status")
    if not isinstance(statefulset_status, Mapping) or statefulset_status.get(
        "readyReplicas"
    ) != 1:
        raise AcceptanceError("Polaris PostgreSQL StatefulSet is not ready")

    jobs = _run_json(
        _kubectl(
            context,
            "get",
            "jobs",
            "-n",
            namespace,
            "-l",
            "app.kubernetes.io/component=catalog-bootstrap",
            "-o",
            "json",
        )
    ).get("items")
    completed_jobs = [
        item
        for item in jobs or []
        if isinstance(item, Mapping) and _complete_condition(item) is not None
    ]
    if not completed_jobs:
        raise AcceptanceError("Polaris bootstrap Job has no completed instance")
    completed_jobs.sort(
        key=lambda item: item.get("metadata", {}).get("creationTimestamp", "")
    )
    job = completed_jobs[-1]

    pvc = _run_json(
        _kubectl(
            context,
            "get",
            "pvc",
            "data-polaris-postgresql-0",
            "-n",
            namespace,
            "-o",
            "json",
        )
    )
    if pvc.get("status", {}).get("phase") != "Bound":
        raise AcceptanceError("Polaris PostgreSQL PVC is not Bound")

    secrets: list[dict[str, Any]] = []
    expected_secret_keys = {
        "polaris-postgresql-credentials": {"database", "username", "password"},
        "polaris-root-credentials": {
            "client_id",
            "client_secret",
            "credentials.json",
            "realm",
        },
    }
    for name, keys in expected_secret_keys.items():
        secret = _run_json(
            _kubectl(context, "get", "secret", name, "-n", namespace, "-o", "json")
        )
        metadata = secret.get("metadata")
        labels = metadata.get("labels") if isinstance(metadata, Mapping) else None
        annotations = metadata.get("annotations") if isinstance(metadata, Mapping) else None
        if not isinstance(labels, Mapping) or labels.get(
            "app.kubernetes.io/managed-by"
        ) != "OpenTofu":
            raise AcceptanceError(f"Secret is not OpenTofu-managed: {name}")
        data = secret.get("data")
        if not isinstance(data, Mapping) or set(data) != keys:
            raise AcceptanceError(f"Secret key contract changed: {name}")
        generation = (
            annotations.get("shirokuma.dev/polaris-credential-generation")
            if isinstance(annotations, Mapping)
            else None
        )
        if not isinstance(generation, str) or not generation.isdigit():
            raise AcceptanceError(f"Secret generation is invalid: {name}")
        secrets.append(
            {
                "name": name,
                "managed_by": "OpenTofu",
                "generation": generation,
                "keys": sorted(keys),
            }
        )
    if len({item["generation"] for item in secrets}) != 1:
        raise AcceptanceError("Polaris Secret generations disagree")

    return {
        "revision": revisions.pop(),
        "kustomizations": kustomizations,
        "workloads": {
            "polaris_deployment": "Ready",
            "postgresql_statefulset": "Ready",
            "bootstrap_job": job.get("metadata", {}).get("name"),
            "postgresql_pvc": "Bound",
        },
        "secrets": secrets,
    }


def _decode_secret(secret: Mapping[str, Any], key: str) -> str:
    data = secret.get("data")
    if not isinstance(data, Mapping) or not isinstance(data.get(key), str):
        raise AcceptanceError(f"required Secret key is missing: {key}")
    try:
        return base64.b64decode(data[key], validate=True).decode("utf-8")
    except (ValueError, UnicodeDecodeError) as error:
        raise AcceptanceError(f"required Secret key is invalid: {key}") from error


def _http_request(
    port: int,
    method: str,
    path: str,
    *,
    headers: Mapping[str, str] | None = None,
    body: bytes = b"",
) -> tuple[int, bytes]:
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    try:
        connection.request(method, path, body=body, headers=dict(headers or {}))
        response = connection.getresponse()
        payload = response.read(1024 * 1024)
        return response.status, payload
    except (OSError, http.client.HTTPException) as error:
        raise AcceptanceError("Polaris API request failed") from error
    finally:
        connection.close()


def _json_object(payload: bytes, operation: str) -> dict[str, Any]:
    try:
        value = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise AcceptanceError(f"Polaris {operation} returned invalid JSON") from error
    if not isinstance(value, dict):
        raise AcceptanceError(f"Polaris {operation} returned a non-object JSON value")
    return value


def _free_local_port() -> int:
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])
    finally:
        listener.close()


def _wait_for_port(port: int, process: subprocess.Popen[bytes]) -> None:
    for _ in range(60):
        if process.poll() is not None:
            raise AcceptanceError("Polaris port-forward exited before readiness")
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                return
        except OSError:
            time.sleep(0.25)
    raise AcceptanceError("Polaris port-forward did not become ready")


def api_smoke(context: str, namespace: str) -> dict[str, Any]:
    secret = _run_json(
        _kubectl(
            context,
            "get",
            "secret",
            "polaris-root-credentials",
            "-n",
            namespace,
            "-o",
            "json",
        )
    )
    client_id = _decode_secret(secret, "client_id")
    client_secret = _decode_secret(secret, "client_secret")
    realm = _decode_secret(secret, "realm")
    port = _free_local_port()
    name = (
        "shirokuma_acceptance_"
        + dt.datetime.now(dt.timezone.utc).strftime("%Y%m%d%H%M%S")
        + "_"
        + os.urandom(3).hex()
    )
    if not CATALOG_NAME.fullmatch(name):
        raise AcceptanceError("generated catalog name is invalid")
    location = f"s3://shirokuma-lakehouse/polaris-acceptance/{name}"
    token: str | None = None
    created = False
    cleanup_status: int | None = None
    process = subprocess.Popen(
        _kubectl(
            context,
            "port-forward",
            "-n",
            namespace,
            "service/polaris",
            f"{port}:8181",
        ),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        _wait_for_port(port, process)
        form = urllib.parse.urlencode(
            {"grant_type": "client_credentials", "scope": "PRINCIPAL_ROLE:ALL"}
        ).encode("ascii")
        authorization = base64.b64encode(
            f"{client_id}:{client_secret}".encode("utf-8")
        ).decode("ascii")
        token_status, token_body = _http_request(
            port,
            "POST",
            "/api/catalog/v1/oauth/tokens",
            headers={
                "Authorization": f"Basic {authorization}",
                "Content-Type": "application/x-www-form-urlencoded",
                "Polaris-Realm": realm,
            },
            body=form,
        )
        token_value = _json_object(token_body, "token")
        token = token_value.get("access_token")
        if token_status != 200 or not isinstance(token, str) or not token:
            raise AcceptanceError("Polaris token request failed")
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Polaris-Realm": realm,
        }
        request = {
            "catalog": {
                "type": "INTERNAL",
                "name": name,
                "properties": {"default-base-location": location},
                "storageConfigInfo": {
                    "storageType": "S3",
                    "allowedLocations": [location],
                    "region": "us-east-1",
                    "endpoint": "http://seaweedfs-s3.shirokuma-storage.svc.cluster.local:8333",
                    "endpointInternal": "http://seaweedfs-s3.shirokuma-storage.svc.cluster.local:8333",
                    "stsUnavailable": True,
                    "pathStyleAccess": True,
                },
            }
        }
        create_status, create_body = _http_request(
            port,
            "POST",
            "/api/management/v1/catalogs",
            headers=headers,
            body=json.dumps(request, separators=(",", ":")).encode("utf-8"),
        )
        if create_status != 201:
            raise AcceptanceError("Polaris catalog create failed")
        created_value = _json_object(create_body, "create")
        if created_value.get("name") != name:
            raise AcceptanceError("Polaris catalog create returned the wrong name")
        created = True

        list_status, list_body = _http_request(
            port, "GET", "/api/management/v1/catalogs", headers=headers
        )
        listed = _json_object(list_body, "list").get("catalogs")
        if list_status != 200 or not isinstance(listed, list) or not any(
            isinstance(item, Mapping) and item.get("name") == name for item in listed
        ):
            raise AcceptanceError("Polaris catalog list did not contain the created catalog")

        read_status, read_body = _http_request(
            port,
            "GET",
            "/api/management/v1/catalogs/" + urllib.parse.quote(name, safe=""),
            headers=headers,
        )
        read_value = _json_object(read_body, "read")
        if (
            read_status != 200
            or read_value.get("name") != name
            or read_value.get("properties", {}).get("default-base-location")
            != location
            or read_value.get("storageConfigInfo", {}).get("storageType") != "S3"
        ):
            raise AcceptanceError("Polaris catalog read did not match the created catalog")

        cleanup_status, _ = _http_request(
            port,
            "DELETE",
            "/api/management/v1/catalogs/" + urllib.parse.quote(name, safe=""),
            headers=headers,
        )
        if cleanup_status != 204:
            raise AcceptanceError("Polaris catalog cleanup failed")
        created = False
        final_status, final_body = _http_request(
            port, "GET", "/api/management/v1/catalogs", headers=headers
        )
        remaining = _json_object(final_body, "post-cleanup list").get("catalogs")
        if final_status != 200 or not isinstance(remaining, list) or any(
            isinstance(item, Mapping) and item.get("name") == name
            for item in remaining
        ):
            raise AcceptanceError("Polaris catalog cleanup was not observable")
        return {
            "catalog_name": name,
            "base_location": location,
            "storage_type": "S3",
            "token_status": token_status,
            "create_status": create_status,
            "list_status": list_status,
            "read_status": read_status,
            "delete_status": cleanup_status,
            "cleanup_absent": True,
            "credential_material_retained": False,
        }
    finally:
        if created and token:
            try:
                _http_request(
                    port,
                    "DELETE",
                    "/api/management/v1/catalogs/"
                    + urllib.parse.quote(name, safe=""),
                    headers={
                        "Authorization": f"Bearer {token}",
                        "Polaris-Realm": realm,
                    },
                )
            except AcceptanceError:
                pass
        if process.poll() is None:
            process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


def _has_symlink_component(path: Path) -> bool:
    absolute = path.expanduser().absolute()
    current = Path(absolute.anchor)
    for part in absolute.parts[1:]:
        current /= part
        if os.path.lexists(current) and current.is_symlink():
            return True
    return False


def host_backup_root(path: Path) -> Path:
    if platform.system() != "Darwin":
        raise AcceptanceError("PostgreSQL backup must be retained on the macOS host")
    if _has_symlink_component(path):
        raise AcceptanceError("backup root must not traverse a symlink")
    try:
        root = path.expanduser().resolve(strict=True)
    except OSError as error:
        raise AcceptanceError("backup root must already exist") from error
    if not root.is_dir():
        raise AcceptanceError("backup root must be a directory")
    temporary = {Path("/tmp").resolve(), Path("/private/tmp").resolve()}
    if any(root == item or item in root.parents for item in temporary):
        raise AcceptanceError("backup root must be durable, not temporary")
    colima = (Path.home() / ".colima").resolve()
    if root == colima or colima in root.parents:
        raise AcceptanceError("backup root must be outside the Colima runtime")
    mode = stat.S_IMODE(root.stat().st_mode)
    if mode & 0o077:
        raise AcceptanceError("backup root permissions must be 0700 or stricter")
    return root


def _postgres_shell(
    context: str,
    namespace: str,
    pod: str,
    script: str,
    *arguments: str,
    input_file: Path | None = None,
    output_file: Path | None = None,
    timeout: int = 300,
) -> bytes:
    command = _kubectl(
        context,
        "exec",
        "-i" if input_file else "--stdin=false",
        "-n",
        namespace,
        pod,
        "--",
        "sh",
        "-ceu",
        script,
        "shirokuma-postgres",
        *arguments,
    )
    stdin: Any = subprocess.DEVNULL
    stdout: Any = subprocess.PIPE
    if input_file:
        stdin = input_file.open("rb")
    if output_file:
        descriptor = os.open(
            output_file,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        stdout = os.fdopen(descriptor, "wb")
    try:
        process = subprocess.run(
            command,
            stdin=stdin,
            stdout=stdout,
            stderr=subprocess.PIPE,
            check=False,
            timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError) as error:
        if output_file:
            output_file.unlink(missing_ok=True)
        raise AcceptanceError("PostgreSQL command failed") from error
    finally:
        if input_file and stdin is not subprocess.DEVNULL:
            stdin.close()
        if output_file and stdout is not subprocess.PIPE:
            stdout.close()
    if process.returncode != 0:
        if output_file:
            output_file.unlink(missing_ok=True)
        raise AcceptanceError("PostgreSQL command failed")
    return process.stdout if isinstance(process.stdout, bytes) else b""


def _quote_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def database_fingerprint(
    context: str, namespace: str, pod: str, database: str
) -> dict[str, Any]:
    if not DATABASE_NAME.fullmatch(database):
        raise AcceptanceError("database name is invalid")
    list_sql = (
        "SELECT n.nspname || E'\\t' || c.relname "
        "FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
        "WHERE c.relkind='r' AND n.nspname NOT IN "
        "('pg_catalog','information_schema') AND n.nspname !~ '^pg_toast' "
        "ORDER BY 1"
    )
    script = (
        'export PGPASSWORD="$POSTGRES_PASSWORD"; '
        'exec psql -X -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" '
        '-d "$1" -At -c "$2"'
    )
    table_output = _postgres_shell(
        context, namespace, pod, script, database, list_sql
    ).decode("utf-8")
    tables: list[tuple[str, str]] = []
    for line in table_output.splitlines():
        parts = line.split("\t")
        if len(parts) != 2 or not all(parts):
            raise AcceptanceError("database table inventory is invalid")
        tables.append((parts[0], parts[1]))
    rows: list[dict[str, Any]] = []
    for schema, table in tables:
        relation = f"{_quote_identifier(schema)}.{_quote_identifier(table)}"
        sql = (
            "SELECT count(*)::text || E'\\t' || "
            "md5(COALESCE(string_agg(row_to_json(t)::text, E'\\n' "
            "ORDER BY row_to_json(t)::text), '')) FROM "
            f"{relation} AS t"
        )
        output = _postgres_shell(
            context, namespace, pod, script, database, sql
        ).decode("utf-8").strip()
        count, separator, digest = output.partition("\t")
        if not separator or not count.isdigit() or not re.fullmatch(r"[0-9a-f]{32}", digest):
            raise AcceptanceError("database row fingerprint is invalid")
        rows.append(
            {"table": f"{schema}.{table}", "row_count": int(count), "md5": digest}
        )
    schema_sql = """
SELECT md5(COALESCE(string_agg(item, E'\\n' ORDER BY item), ''))
FROM (
  SELECT 'column|' || table_schema || '|' || table_name || '|' ||
         ordinal_position || '|' || column_name || '|' || data_type || '|' ||
         udt_name || '|' || is_nullable || '|' || COALESCE(column_default, '') AS item
  FROM information_schema.columns
  WHERE table_schema NOT IN ('pg_catalog', 'information_schema')
  UNION ALL
  SELECT 'index|' || schemaname || '|' || tablename || '|' || indexname || '|' || indexdef
  FROM pg_indexes
  WHERE schemaname NOT IN ('pg_catalog', 'information_schema')
  UNION ALL
  SELECT 'constraint|' || n.nspname || '|' || c.relname || '|' || con.conname || '|' ||
         pg_get_constraintdef(con.oid, true)
  FROM pg_constraint con
  JOIN pg_class c ON c.oid = con.conrelid
  JOIN pg_namespace n ON n.oid = c.relnamespace
  WHERE n.nspname NOT IN ('pg_catalog', 'information_schema')
  UNION ALL
  SELECT 'sequence|' || schemaname || '|' || sequencename || '|' ||
         COALESCE(last_value::text, '')
  FROM pg_sequences
  WHERE schemaname NOT IN ('pg_catalog', 'information_schema')
) AS metadata
""".strip()
    schema_digest = _postgres_shell(
        context, namespace, pod, script, database, schema_sql
    ).decode("utf-8").strip()
    if not re.fullmatch(r"[0-9a-f]{32}", schema_digest):
        raise AcceptanceError("database schema fingerprint is invalid")
    canonical = json.dumps(
        {"schema_md5": schema_digest, "tables": rows},
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return {
        "schema_md5": schema_digest,
        "table_count": len(rows),
        "row_count": sum(item["row_count"] for item in rows),
        "content_sha256": hashlib.sha256(canonical).hexdigest(),
    }


def backup_restore(
    context: str, namespace: str, backup_root: Path
) -> dict[str, Any]:
    root = host_backup_root(backup_root)
    pods = _run_json(
        _kubectl(
            context,
            "get",
            "pods",
            "-n",
            namespace,
            "-l",
            "app.kubernetes.io/name=polaris-postgresql",
            "-o",
            "json",
        )
    ).get("items")
    running = [
        item
        for item in pods or []
        if isinstance(item, Mapping) and item.get("status", {}).get("phase") == "Running"
    ]
    if len(running) != 1:
        raise AcceptanceError("exactly one running Polaris PostgreSQL Pod is required")
    pod = running[0].get("metadata", {}).get("name")
    if not isinstance(pod, str) or not pod:
        raise AcceptanceError("Polaris PostgreSQL Pod name is missing")
    captured = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup = root / f"polaris-postgresql-{captured}.dump"
    temporary_database = (
        "polaris_restore_"
        + dt.datetime.now(dt.timezone.utc).strftime("%Y%m%d%H%M%S")
        + "_"
        + os.urandom(3).hex()
    )
    if not DATABASE_NAME.fullmatch(temporary_database):
        raise AcceptanceError("generated restore database name is invalid")
    dump_script = (
        'export PGPASSWORD="$POSTGRES_PASSWORD"; '
        'exec pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" '
        "--format=custom --compress=9 --no-owner --no-privileges"
    )
    source_before = database_fingerprint(context, namespace, pod, "polaris")
    _postgres_shell(
        context,
        namespace,
        pod,
        dump_script,
        output_file=backup,
        timeout=600,
    )
    try:
        with backup.open("rb") as stream:
            os.fsync(stream.fileno())
        directory_descriptor = os.open(root, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(directory_descriptor)
        finally:
            os.close(directory_descriptor)
    except OSError as error:
        backup.unlink(missing_ok=True)
        raise AcceptanceError("cannot sync PostgreSQL backup") from error
    backup_stat = backup.stat()
    if backup_stat.st_size <= 0 or stat.S_IMODE(backup_stat.st_mode) != 0o600:
        backup.unlink(missing_ok=True)
        raise AcceptanceError("PostgreSQL backup file contract failed")
    digest = hashlib.sha256()
    with backup.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    archive_list = _postgres_shell(
        context,
        namespace,
        pod,
        "exec pg_restore --list",
        input_file=backup,
    ).decode("utf-8")
    archive_entries = sum(
        1
        for line in archive_list.splitlines()
        if line.strip() and not line.lstrip().startswith(";")
    )
    if archive_entries <= 0:
        backup.unlink(missing_ok=True)
        raise AcceptanceError("PostgreSQL backup archive inventory is empty")
    source = database_fingerprint(context, namespace, pod, "polaris")
    if source != source_before:
        backup.unlink(missing_ok=True)
        raise AcceptanceError("source database changed while backup was captured")
    create_script = (
        'export PGPASSWORD="$POSTGRES_PASSWORD"; '
        'exec createdb -U "$POSTGRES_USER" "$1"'
    )
    drop_script = (
        'export PGPASSWORD="$POSTGRES_PASSWORD"; '
        'exec dropdb --if-exists --force -U "$POSTGRES_USER" "$1"'
    )
    restore_script = (
        'export PGPASSWORD="$POSTGRES_PASSWORD"; '
        'exec pg_restore --exit-on-error --single-transaction '
        '--no-owner --no-privileges '
        '-U "$POSTGRES_USER" -d "$1"'
    )
    restored: dict[str, Any] | None = None
    cleanup_complete = False
    try:
        _postgres_shell(
            context, namespace, pod, create_script, temporary_database
        )
        _postgres_shell(
            context,
            namespace,
            pod,
            restore_script,
            temporary_database,
            input_file=backup,
            timeout=600,
        )
        restored = database_fingerprint(
            context, namespace, pod, temporary_database
        )
        if restored != source:
            raise AcceptanceError("restored PostgreSQL fingerprint differs from source")
    finally:
        try:
            _postgres_shell(
                context, namespace, pod, drop_script, temporary_database
            )
            cleanup_complete = True
        except AcceptanceError:
            cleanup_complete = False
    if not cleanup_complete or restored is None:
        raise AcceptanceError("temporary restore database cleanup failed")
    version_script = "exec pg_dump --version"
    version = _postgres_shell(
        context, namespace, pod, version_script
    ).decode("utf-8").strip()
    disk = shutil.disk_usage(root)
    return {
        "backup_file": backup.name,
        "backup_sha256": digest.hexdigest(),
        "backup_bytes": backup_stat.st_size,
        "backup_mode": "0600",
        "host_root_mode": f"{stat.S_IMODE(root.stat().st_mode):04o}",
        "host_free_kib": disk.free // 1024,
        "postgresql_tools": version,
        "archive_entries": archive_entries,
        "source_fingerprint": source,
        "restored_fingerprint": restored,
        "fingerprints_match": True,
        "temporary_database_removed": True,
        "backup_location_policy": "durable macOS host outside Colima",
    }


def _write_receipt(path: Path, receipt: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(receipt, stream, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o644)
        os.replace(temporary, path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def run_acceptance(args: argparse.Namespace) -> dict[str, Any]:
    if shutil.which("kubectl") is None:
        raise AcceptanceError("kubectl is required")
    readiness = collect_readiness(args.context, args.namespace)
    smoke = api_smoke(args.context, args.namespace)
    recovery = backup_restore(args.context, args.namespace, args.backup_root)
    revision = _run(["git", "rev-parse", "HEAD"]).stdout.decode("ascii").strip()
    if not GIT_SHA.fullmatch(revision):
        raise AcceptanceError("repository revision is invalid")
    receipt = {
        "schema_version": 1,
        "kind": "shirokuma-polaris-runtime-acceptance",
        "issue": 61,
        "acceptance_tool_sha256": hashlib.sha256(
            Path(__file__).read_bytes()
        ).hexdigest(),
        "captured_at": dt.datetime.now(dt.timezone.utc)
        .replace(microsecond=0)
        .isoformat(),
        "repository_revision": revision,
        "cluster": {
            "context": args.context,
            "namespace": args.namespace,
            "profile": "local-lite",
            "production_claim": False,
        },
        "readiness": readiness,
        "catalog_api_smoke": smoke,
        "backup_restore": recovery,
        "rollback_teardown": {
            "verified_by": "static contract and focused tests",
            "runbooks": [
                "docs/design/08_Runbooks/RB-001_Bootstrap_local_lite_lab.md",
                "docs/design/08_Runbooks/RB-013_Nuke_and_Rebuild_mac_studio_solo.md",
            ],
            "destructive_teardown_executed": False,
        },
        "secrets": {
            "material_in_receipt": False,
            "material_in_git": False,
            "provisioner": "OpenTofu",
        },
    }
    _write_receipt(args.output, receipt)
    return receipt


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--context", default=DEFAULT_CONTEXT)
    parser.add_argument("--namespace", default=DEFAULT_NAMESPACE)
    parser.add_argument("--backup-root", type=Path, default=DEFAULT_BACKUP_ROOT)
    parser.add_argument("--output", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        receipt = run_acceptance(args)
    except AcceptanceError as error:
        print(f"polaris-runtime-acceptance: {error}", file=sys.stderr)
        return 1
    summary = {
        "output": str(args.output),
        "revision": receipt["readiness"]["revision"],
        "catalog_api_smoke": "passed",
        "backup_restore": "passed",
        "secrets_retained": False,
    }
    print(json.dumps(summary, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
