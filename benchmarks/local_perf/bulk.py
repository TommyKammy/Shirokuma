"""Optional, one-shot native Parquet registration for owned benchmark campaigns.

No recovery replay or remote cleanup is provided. An uncertain mutation retains
its INTENT and UNKNOWN evidence for the commander to inspect. Dependencies are
imported only by SDK(), so ordinary benchmark/test imports remain stdlib-only.
"""
import copy
import hashlib
import ipaddress
import importlib.metadata
import json
import math
import os
from pathlib import Path
import re
import stat
import time
import urllib.parse
import uuid

from .capacity import write_json
from .dataset_layout import file_identity, table_files
from .ingest import (absent, budget, child, file_hash, owned_record,
                     public_client, resolve, target_name)
from .protocol import LIMIT, settings
from .results import column

CHUNK = 1024 * 1024
# Arrow 26.0.1 uses fixed 10 MiB S3 parts, with at most 10,000 parts.
MAX_SOURCE_FILE_BYTES = 10000 * 10 * 1024**2
AUTH_KEYS = {"format", "uri", "warehouse", "realm", "client_id", "client_secret",
             "storage_endpoint", "region", "database_id", "storage_prefix"}
OWNER = "shirokuma.benchmark.owner"
SOURCE = "shirokuma.benchmark.source-sha256"


def _version(package):
    try:
        return importlib.metadata.version(package)
    except importlib.metadata.PackageNotFoundError:
        return "unavailable"


def _endpoint(value, catalog=False):
    try:
        p = urllib.parse.urlsplit(value)
        port = p.port
        try:
            local = p.hostname == "localhost" or ipaddress.ip_address(p.hostname).is_loopback
        except ValueError:
            local = False
    except (ValueError, TypeError):
        raise ValueError("explicit loopback endpoint required") from None
    if (not local or p.scheme not in ("http", "https") or p.username is not None
            or p.password is not None or p.query or p.fragment
            or p.path != ("/api/catalog" if catalog else "") or (port is None and not catalog)
            or (port is not None and not 1 <= port <= 65535) or any(c.isspace() for c in value)):
        raise ValueError("explicit loopback endpoint required")
    return value


def _s3(value):
    if not isinstance(value, str):
        raise ValueError("S3 location required")
    p = urllib.parse.urlsplit(value)
    if (p.scheme != "s3" or not re.fullmatch(r"[a-z0-9][a-z0-9.-]*", p.netloc)
            or p.query or p.fragment or not p.path.startswith("/")
            or any(x in (".", "..") for x in p.path.split("/"))
            or "//" in p.path or re.search(r"[%\\\s\x00-\x1f]", p.path)):
        raise ValueError("plain owned S3 location required")
    return p


def _auth(path, ctx):
    path = Path(path)
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o600:
        raise ValueError("explicit regular 0600 auth file required")
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate auth key")
            result[key] = value
        return result
    a = json.loads(path.read_text(), object_pairs_hook=unique)
    if (not isinstance(a, dict) or set(a) != AUTH_KEYS or type(a["format"]) is not int
            or a["format"] != 1 or any(not isinstance(a[k], str) or not a[k]
                or any(ord(c) < 32 for c in a[k]) for k in AUTH_KEYS - {"format"})):
        raise ValueError("invalid explicit bulk auth")
    _endpoint(a["uri"], True)
    _endpoint(a["storage_endpoint"])
    p = _s3(a["storage_prefix"])
    if (a["warehouse"] != ctx["sql_catalog"] or a["database_id"] != ctx["database_id"]
            or p.path != "/" + ctx["database_id"] + "/"
            or not re.fullmatch(r"[A-Za-z0-9_-]+", a["database_id"])
            or not re.fullmatch(r"[A-Za-z0-9_-]+", a["region"])
            or ":" in a["client_id"]):
        raise ValueError("DB-scoped consumer auth required")
    return a


def _columns(schema):
    cols = [column(c["name"], c["type"]) for c in schema]
    if (not cols or len({c["name"] for c in cols}) != len(cols)
            or any(not isinstance(c["name"], str) or not c["name"] or "\x00" in c["name"] for c in cols)):
        raise ValueError("unique scalar columns required")
    # Iceberg has int32/int64, microsecond timestamp and decimal <=38 only.
    for c in cols:
        if c["type"] not in {"integer", "bigint", "varchar", "boolean", "date", "real", "double",
                             "timestamp(6)", "timestamp(6) with time zone"} and not c["type"].startswith("decimal("):
            raise ValueError("unsupported exact Iceberg scalar contract")
    return cols


def _arrow_columns(schema, pa):
    cols = []
    for f in schema:
        t, types = f.type, pa.types
        kind = None
        for test, name in ((types.is_int32, "integer"), (types.is_int64, "bigint"),
                           (types.is_string, "varchar"), (types.is_large_string, "varchar"),
                           (types.is_boolean, "boolean"), (types.is_date32, "date"),
                           (types.is_float32, "real"), (types.is_float64, "double")):
            if test(t):
                kind = name
                break
        if types.is_decimal128(t):
            kind = "decimal(%d,%d)" % (t.precision, t.scale)
        if types.is_timestamp(t) and t.unit == "us" and t.tz in (None, "UTC"):
            kind = "timestamp(6)" + (" with time zone" if t.tz else "")
        if kind is None or (f.metadata or {}).get(b"PARQUET:field_id") is not None:
            raise ValueError("unsupported Parquet scalar or existing field IDs")
        cols.append(column(f.name, kind))
    return _columns(cols)


def _remaining(deadline):
    value = deadline - time.monotonic()
    if value <= 0:
        raise TimeoutError("bulk deadline")
    return min(30, value)


class SDK:
    """Pinned SDK boundary; tests replace this class without importing SDKs.

    0.12.0 RestCatalog uses requests.Session and tenacity auth retries (there is
    no RESTSession class). Bypass only create/commit's decorated wrappers; keep
    standard serialization/response handling. Never patch upstream globally.
    """
    def __init__(self):
        import pyarrow as pa
        import pyarrow.parquet as pq
        import pyiceberg
        if pa.__version__ != "26.0.1" or pyiceberg.__version__ != "0.12.0":
            raise ValueError("pinned optional bulk dependencies required")
        self.pa, self.pq = pa, pq

    def footer(self, path):
        with self.pq.ParquetFile(path) as f:
            return f.schema_arrow, _arrow_columns(f.schema_arrow, self.pa), f.metadata.num_rows

    def catalog(self, auth, deadline, check):
        from requests import Session
        from requests.adapters import HTTPAdapter
        from pyiceberg.catalog.rest import RestCatalog
        from pyiceberg.catalog.rest.auth import LegacyOAuth2AuthManager, AuthManagerAdapter
        from pyiceberg.io.pyarrow import PyArrowFileIO
        from pyarrow.fs import S3FileSystem, AwsStandardS3RetryStrategy

        class OwnedIO(PyArrowFileIO):
            def __init__(self, properties, table_location):
                super().__init__(properties)
                self.table_location = table_location

            def _owned(self, location):
                check()
                _s3(location)
                if not location.startswith(self.table_location + "/"):
                    raise ValueError("IO outside created table")

            def new_input(self, location):
                self._owned(location)
                return super().new_input(location)

            def new_output(self, location):
                self._owned(location)
                return super().new_output(location)

            def delete(self, location):
                # SDK abort/metadata-retention hooks must retain evidence too.
                self._owned(location if isinstance(location, str) else location.location)

            def _initialize_s3_fs(self, netloc):
                check()
                p = self.properties
                return S3FileSystem(access_key=p["s3.access-key-id"], secret_key=p["s3.secret-access-key"],
                    session_token=p["s3.session-token"], region=auth["region"],
                    endpoint_override=auth["storage_endpoint"], background_writes=False,
                    connect_timeout=_remaining(deadline), request_timeout=_remaining(deadline),
                    retry_strategy=AwsStandardS3RetryStrategy(max_attempts=1))

        class OneShotCatalog(RestCatalog):
            def create_table(self, *args, **kwargs):
                self._creating = True
                try:
                    return RestCatalog.create_table.__wrapped__(self, *args, **kwargs)
                finally:
                    self._creating = False

            load_table = RestCatalog.load_table.__wrapped__
            def commit_table(self, *args, **kwargs):
                response = RestCatalog.commit_table.__wrapped__(self, *args, **kwargs)
                capture = getattr(self, "capture_commit", None)
                if capture is not None:
                    capture(response.metadata)
                return response

            def _response_to_table(self, identifier_tuple, response):
                creating = getattr(self, "_creating", False)
                if creating:
                    self.capture_create(response.metadata.table_uuid)
                elif (getattr(self, "_expected_created_uuid", None) is not None
                      and response.metadata.table_uuid != self._expected_created_uuid):
                    raise ValueError("created/load UUID mismatch")
                location = response.metadata.location.rstrip("/")
                _s3(location)
                if not location.startswith(auth["storage_prefix"]):
                    raise ValueError("created table outside DB storage prefix")
                self._table_location = location
                credentials = self._resolve_storage_credentials(
                    response.storage_credentials, response.metadata_location)
                config = {**response.config, **credentials}
                keys = ("s3.access-key-id", "s3.secret-access-key", "s3.session-token")
                if creating and any(not config.get(k) for k in keys):
                    self._creating = False
                    self._expected_created_uuid = response.metadata.table_uuid
                    try:
                        loaded = self.load_table(identifier_tuple)
                        if loaded.location().rstrip("/") != location:
                            raise ValueError("created/load location mismatch")
                        return loaded
                    finally:
                        self._expected_created_uuid = None
                return super()._response_to_table(identifier_tuple, response)

            def _create_session(self):
                session = Session()
                session.trust_env = False  # no netrc/proxies/ambient auth
                session.mount("http://", HTTPAdapter(max_retries=0))
                session.mount("https://", HTTPAdapter(max_retries=0))
                self._config_headers(session)
                request = session.request
                def bounded_request(method, url, **kw):
                    check()
                    p, base = urllib.parse.urlsplit(url), urllib.parse.urlsplit(auth["uri"])
                    if (p.scheme, p.netloc) != (base.scheme, base.netloc) or not p.path.startswith(base.path + "/"):
                        raise ValueError("catalog endpoint drift")
                    kw.update(timeout=_remaining(deadline), allow_redirects=False)
                    response = request(method, url, **kw)
                    if 300 <= response.status_code < 400:
                        raise ValueError("catalog redirect rejected")
                    return response
                session.request = bounded_request
                manager = LegacyOAuth2AuthManager(session, auth_url=auth["uri"] + "/v1/oauth/tokens",
                    credential=auth["client_id"] + ":" + auth["client_secret"],
                    optional_oauth_params={"scope": "PRINCIPAL_ROLE:ALL"})
                self._auth_manager = manager
                session.auth = AuthManagerAdapter(manager)
                return session

            def _fetch_config(self):
                explicit = dict(self.properties)
                super()._fetch_config()
                prefix = self.properties.get("prefix", "")
                if prefix and (not re.fullmatch(r"[A-Za-z0-9_/-]+", prefix)
                               or any(x in (".", "..") for x in prefix.split("/"))):
                    raise ValueError("catalog prefix invalid")
                self.properties = {**explicit, "prefix": prefix}
                self.uri = auth["uri"]

            def _load_file_io(self, properties=None, location=None):
                # Deliberately exclude catalog/default properties and AWS fallback.
                p = properties or {}
                keys = ("s3.access-key-id", "s3.secret-access-key", "s3.session-token")
                if any(not isinstance(p.get(k), str) or not p[k].strip() for k in keys):
                    raise ValueError("table-vended STS credentials required")
                return OwnedIO({k: p[k] for k in keys}, self._table_location)

        return OneShotCatalog("benchmark-bulk", uri=auth["uri"], warehouse=auth["warehouse"],
            **{"header.Polaris-Realm": auth["realm"],
               "header.X-Iceberg-Access-Delegation": "vended-credentials"})

    def schema(self, table):
        from pyiceberg.io.pyarrow import schema_to_pyarrow
        return _arrow_columns(schema_to_pyarrow(table.schema(), include_field_ids=False), self.pa)


def _location(table, auth):
    value = table.location().rstrip("/")
    _s3(value)
    if not value.startswith(auth["storage_prefix"]) or value == auth["storage_prefix"].rstrip("/"):
        raise ValueError("created table outside DB storage prefix")
    return value


def _copy(source, output, expected, check):
    h, size = hashlib.sha256(), 0
    with source.open("rb") as src, output.create(overwrite=False) as dst:
        while True:
            check()
            block = src.read(CHUNK)
            if not block:
                break
            dst.write(block)
            size += len(block)
            h.update(block)
    check()
    if h.hexdigest() != expected["sha256"] or size != expected["bytes"]:
        raise ValueError("source changed during copy")
    return size


def load_bulk(campaign, dataset, name, prefix, adapter, auth_file, *, timeout=600, peak_bytes=None):
    """Copy immutable Parquet to fresh owned tables and call add_files once/table.

    Auth JSON has exactly AUTH_KEYS, format=1, and must be a regular mode-0600
    file. No credential paths are discovered and credentials never enter receipts.
    Returns the durable load.json Path; any exception retains local/remote evidence.
    """
    identity, ctx = public_client(adapter)
    identity = copy.deepcopy(identity)
    if ctx["selected_role"] not in ("admin", "data_engineer") or ctx["sql_schema"] not in ("raw", "sandbox"):
        raise ValueError("manual ingest role/namespace required")
    auth = _auth(auth_file, ctx)
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("positive finite timeout required")
    source, meta = owned_record(campaign, dataset, "dataset.json", "dataset")
    cfg = settings(mode=meta.get("mode", "qualification"), scale=meta.get("scale", .01),
                   peak=peak_bytes if peak_bytes is not None else meta.get("peak_bytes"),
                   timeout=timeout, repetitions=1, peak_evidence=meta.get("peak_evidence"))
    if (type(meta.get("format")) is not int or meta["format"] not in (1, 2)
            or not isinstance(meta.get("dataset_id"), str) or not meta["dataset_id"].strip()
            or type(meta.get("measured_logical_bytes")) is not int
            or not 0 <= meta["measured_logical_bytes"] <= (1_000_000_000 if cfg["mode"] == "qualification" else LIMIT)):
        raise ValueError("source exceeds protocol limit")
    campaign.refresh()
    if any(e["kind"] == "bulk-load" and e["status"] in ("INCOMPLETE", "UNKNOWN")
           for e in campaign.state["owned"].values()):
        raise ValueError("prior bulk load unresolved")
    campaign.check(cfg["peak_bytes"])
    root = campaign.own(name, "bulk-load")
    root.chmod(0o700)
    path, token = root / "load.json", uuid.uuid4().hex
    journal = dict(format=1, kind="bulk-load", status="INCOMPLETE", source_dataset_id=meta["dataset_id"],
                   context=ctx, adapter_identity=identity, ownership_token=token, settings=cfg,
                   tables=[], operations=[], source_manifest_sha256=None,
                   sdk_versions={package: _version(package) for package in ("pyiceberg", "pyarrow")},
                   retry_attempts={"create": 1, "commit": 1, "http": 1, "s3": 1, "load_after_create": 1})
    def save():
        write_json(path, journal)
        path.chmod(0o600)
        fd = os.open(root, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    save()
    started = time.monotonic()
    deadline = started + timeout
    capacity_check = budget(campaign, root, cfg, deadline)
    def check():
        capacity_check()
        if public_client(adapter) != (identity, ctx):
            raise ValueError("frozen public identity/context changed")
    def public_check():
        check()
        adapter.select(ctx["database_id"], ctx["schema_id"], timeout=_remaining(deadline))
        check()
        schemas = adapter.request("GET", adapter.url + "/api/v1/databases/" +
            urllib.parse.quote(ctx["database_id"], safe="") + "/schemas", deadline)["items"]
        selected = [s for s in schemas if s.get("id") == ctx["schema_id"]]
        if (len(selected) != 1 or selected[0].get("sql_name") != ctx["sql_schema"]
                or selected[0].get("capabilities", {}).get("manual_ingest") is not True):
            raise ValueError("fresh schema manual ingest capability required")
        check()
    def intent(kind, item, **details):
        check()
        op = dict(kind=kind, table=item["name"], status="INTENT", **details)
        journal["operations"].append(op)
        save()
        return op
    catalog = None
    try:
        check()
        sdk = SDK()
        manifest_sha = file_hash(source / "dataset.json", check)
        if json.loads((source / "dataset.json").read_text()) != meta:
            raise ValueError("manifest changed")
        journal["source_manifest_sha256"] = manifest_sha
        version = meta["format"]
        # Stat every raw reference before any per-file hashing/footer work. The
        # shared layout validator deliberately has no transport size policy.
        seen_paths = set()
        for t in meta["tables"]:
            raw_files = t.get("files", []) if version == 2 else [t]
            for record in raw_files:
                if version == 2 and set(record) != {"file", "rows", "file_bytes", "sha256", "physical_bytes"}:
                    raise ValueError("exact source file metadata required")
                local = child(source, record["file"])
                size = local.stat().st_size
                if size > MAX_SOURCE_FILE_BYTES:
                    raise ValueError("source file exceeds S3 multipart size limit")
                resolved = local.resolve()
                if resolved in seen_paths:
                    raise ValueError("duplicate source path")
                seen_paths.add(resolved)
        def source_hash(local, expected_size, message):
            size = local.stat().st_size
            if size > MAX_SOURCE_FILE_BYTES:
                raise ValueError("source file exceeds S3 multipart size limit")
            if size != expected_size:
                raise ValueError(message)
            return file_hash(local, check)
        plans, names = [], set()
        for t in meta["tables"]:
            physical = target_name(prefix, t["name"])
            if physical in names:
                raise ValueError("duplicate target")
            names.add(physical)
            cols = _columns(t["schema"])
            parts = table_files(source, t, version=version, check=check)
            arrow, total_rows = None, 0
            for part in parts:
                local = part["path"]
                size = local.stat().st_size
                sha = source_hash(local, part["file_bytes"], "source schema/rows/hash mismatch")
                part_arrow, actual, rows = sdk.footer(local)
                if (actual != cols or rows != part["rows"] or part["file_bytes"] != size
                        or (part.get("sha256") is not None and part["sha256"] != sha) or source_hash(local, size, "source schema/rows/hash mismatch") != sha):
                    raise ValueError("source schema/rows/hash mismatch")
                if arrow is None:
                    arrow = part_arrow
                elif not (arrow.equals(part_arrow, check_metadata=False)
                          if hasattr(arrow, "equals") else arrow == part_arrow):
                    raise ValueError("source part schema mismatch")
                total_rows += rows
                part.update(sha256=sha, bytes=size)
            if total_rows != t["rows"]:
                raise ValueError("source row sum mismatch")
            sha = file_identity(parts, version=version)
            item = dict(name=t["name"], physical_name=physical, schema=cols,
                        schema_hints={c["name"]: c["type"].upper() for c in cols},
                        source_sha256=sha, rows=total_rows, committed_rows=0, ref=None, cleanup_clean=None,
                        uuid=None, snapshot=None, uploadedfiles=[])
            if version == 2:
                item["source_files"] = [{k: p[k] for k in
                    ("file", "rows", "file_bytes", "sha256", "physical_bytes")} for p in parts]
            journal["tables"].append(item)
            plans.append((item, parts, arrow))
            check()
        if not plans or sum(p["bytes"] for _, parts, _ in plans for p in parts) > cfg["peak_bytes"]:
            raise ValueError("empty manifest or source exceeds copy peak")
        def manifest_immutable(message):
            if file_hash(source / "dataset.json", check) != manifest_sha:
                raise ValueError("manifest changed" + message)
        def immutable(message, selected=None):
            manifest_immutable(message)
            for part in (selected if selected is not None else
                         [p for _, group, _ in plans for p in group]):
                if source_hash(part["path"], part["bytes"], "source changed" + message) != part["sha256"]:
                    raise ValueError("source changed" + message)
        immutable("")
        save()
        public_check()
        absent(adapter, names, deadline)
        check()
        catalog = sdk.catalog(auth, deadline, check)
        for item, parts, arrow in plans:
            public_check()
            absent(adapter, {item["physical_name"]}, deadline)
            immutable(" before create", parts)
            props = {"format-version": "2", "commit.retry.num-retries": "0", OWNER: token,
                     SOURCE: item["source_sha256"]}
            op = intent("create", item, properties=props)
            def capture_create(value):
                candidate = str(value)
                item["uuid_candidate"] = candidate if re.fullmatch(r"[A-Fa-f0-9-]{1,36}", candidate) else "INVALID"
                save()
                parsed = uuid.UUID(candidate)
                if not parsed.int or str(parsed) != candidate:
                    raise ValueError("canonical nonzero created UUID required")
                if item["uuid"] is not None and item["uuid"] != candidate:
                    raise ValueError("created/returned UUID mismatch")
                item["uuid"] = candidate
                op["status"] = "RETURNED"
                save()
            catalog.capture_create = capture_create
            table = catalog.create_table((ctx["sql_schema"], item["physical_name"]), schema=arrow, properties=props)
            capture_create(table.metadata.table_uuid)
            current_schema_id = table.metadata.current_schema_id
            if type(current_schema_id) is not int or current_schema_id < 0:
                raise ValueError("nonnegative integer current schema ID required")
            location = _location(table, auth)
            item["location"] = location
            save()
            item["returned_schema"] = sdk.schema(table)
            if (item["returned_schema"] != item["schema"] or table.current_snapshot() is not None
                    or any(table.properties.get(k) != v for k, v in props.items() if k != "format-version")
                    or table.metadata.format_version != 2):
                raise ValueError("created table identity/schema/properties mismatch")
            def validate_empty(candidate):
                if (str(candidate.metadata.table_uuid) != item["uuid"]
                        or type(candidate.metadata.current_schema_id) is not int
                        or candidate.metadata.current_schema_id != current_schema_id
                        or _location(candidate, auth) != location
                        or sdk.schema(candidate) != item["schema"]
                        or candidate.current_snapshot() is not None
                        or candidate.metadata.format_version != 2
                        or any(candidate.properties.get(k) != v for k, v in props.items()
                               if k != "format-version")):
                    raise ValueError("refreshed table identity/schema/properties/snapshot mismatch")
                return candidate
            targets = []
            for ordinal, part in enumerate(parts):
                public_check()
                if version == 2 and ordinal:
                    table = validate_empty(catalog.load_table((ctx["sql_schema"], item["physical_name"])))
                immutable(" before upload", [part])
                suffix = (str(ordinal) + "-" if version == 2 else "") + part["sha256"]
                target = location + "/data/" + token + "-" + suffix + ".parquet"
                targets.append(target)
                uploaded = dict(location=target, sha256=part["sha256"], bytes=part["bytes"], status="INTENT")
                item["uploadedfiles"].append(uploaded)
                op = intent("upload", item, location=target, sha256=part["sha256"], bytes=part["bytes"])
                # Persist intent before even constructing the remote output.
                output = table.io.new_output(target)
                began = time.monotonic()
                copied = _copy(part["path"], output, part, check)
                uploaded.update(status="SUCCESS", copied_bytes=copied, elapsed_s=time.monotonic() - began)
                op["status"] = "RETURNED"
                save()
                immutable(" after copy", [part])
            public_check()
            if version == 2:
                table = validate_empty(catalog.load_table((ctx["sql_schema"], item["physical_name"])))
            immutable(" before commit", parts)
            op = intent("commit", item, uuid=item["uuid"], files=targets)
            began = time.monotonic()
            def capture_commit(metadata):
                op["acknowledgement"] = "RETURNED"
                save()
                snapshot = metadata.current_snapshot()
                if snapshot is not None:
                    sid = snapshot.snapshot_id
                    item["snapshot"] = sid if type(sid) is int else None
                    save()
            catalog.capture_commit = capture_commit
            snapshot_props = {OWNER: token, SOURCE: item["source_sha256"]}
            table.add_files(targets, snapshot_properties=snapshot_props, check_duplicate_files=True)
            op["acknowledgement"] = "RETURNED"
            item["commit_elapsed_s"] = time.monotonic() - began
            save()
            snapshot = table.current_snapshot()
            if snapshot is None:
                raise ValueError("missing returned snapshot")
            sid = snapshot.snapshot_id
            item["snapshot"] = sid if type(sid) is int else None
            save()
            summary = snapshot.summary.additional_properties
            required = {OWNER: token, SOURCE: item["source_sha256"],
                        "total-records": str(item["rows"]),
                        "total-data-files": str(len(parts)), "added-data-files": str(len(parts))}
            added_valid = (summary.get("added-records") == str(item["rows"]) if item["rows"] else
                           "added-records" not in summary or summary["added-records"] == "0")
            if (str(table.metadata.table_uuid) != item["uuid"]
                    or not added_valid
                    or type(sid) is not int or not 0 < sid <= 2**63 - 1
                    or any(summary.get(k) != v for k, v in required.items())
                    or getattr(snapshot.summary.operation, "value", snapshot.summary.operation) != "append"):
                raise ValueError("returned commit identity/summary mismatch")
            expected_files = {target: dict(location=target, record_count=part["rows"],
                file_size_in_bytes=part["bytes"]) for target, part in zip(targets, parts)}
            proofs = {}
            scan = table.scan(options={"include_empty_files": "true"})
            for task in scan.plan_files():
                f = task.file
                proof = dict(location=f.file_path, record_count=f.record_count, file_size_in_bytes=f.file_size_in_bytes)
                if (f.file_path in proofs or proof != expected_files.get(f.file_path)
                        or type(f.record_count) is not int or type(f.file_size_in_bytes) is not int):
                    raise ValueError("registered file metadata mismatch")
                proofs[f.file_path] = proof
            if proofs.keys() != expected_files.keys():
                raise ValueError("missing registered native files")
            item["registeredfiles"] = [proofs[target] for target in targets]
            save()
            item["committed_schema"] = sdk.schema(table)
            if (str(table.metadata.table_uuid) != item["uuid"] or _location(table, auth) != location
                    or type(table.metadata.current_schema_id) is not int
                    or table.metadata.current_schema_id != current_schema_id
                    or item["committed_schema"] != item["schema"]):
                raise ValueError("committed table identity/schema changed")
            if version == 2:
                validate_properties = {k: v for k, v in props.items() if k != "format-version"}
                if (table.metadata.format_version != 2
                        or any(table.properties.get(k) != v for k, v in validate_properties.items())):
                    raise ValueError("committed properties changed")
            item["committed_rows"] = item["rows"]
            op["status"] = "RETURNED"
            save()
            if version == 2:
                immutable(" after commit", parts)
            public_check()
            item["ref"] = resolve(adapter, item["physical_name"], deadline)
            check()
            item["cleanup_clean"] = True
            save()
        public_check()
        immutable(" after commit")
        journal["status"] = "SUCCESS"
        return path
    except BaseException as exc:
        error_type = type(exc).__name__
        journal["error_type"] = error_type if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,79}", error_type) else "Exception"
        journal["status"] = "UNKNOWN" if any(o["status"] == "INTENT" for o in journal["operations"]) else "FAILED"
        raise
    finally:
        journal["elapsed_s"] = time.monotonic() - started
        save()
        campaign.mark(root, journal["status"])
        if catalog is not None:
            catalog._session.close()
