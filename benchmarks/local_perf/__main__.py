"""Explicit local commands; none apply VM/workload changes."""
import argparse
import json
import math
import os
from pathlib import Path

from .capacity import Campaign, guest_requirement, write_json
from .dataset import generate, prepare_reference, qualify_packaged_tpch, prepare_sql_reference
from .protocol import GIB, plan, protocol
from .public import PublicAdapter, client_auth_path, private_auth
from .results import compare
from .runner import DuckDBAdapter, TrinoAdapter, reconcile, run


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    p = commands.add_parser("protocol")
    p.add_argument("--profile", default="L1", choices=list(protocol()["profiles"]))
    p = commands.add_parser("init")
    p.add_argument("campaign")
    p.add_argument("--image", action="append", default=[])
    p.add_argument("--host-path", action="append", default=[])
    p.add_argument("--drain-bytes", type=int, default=64 * 1024 ** 2)
    p = commands.add_parser("preflight")
    p.add_argument("campaign")
    p.add_argument("--additional-bytes", type=int, default=0)
    p.add_argument("--guest-snapshot", help="JSON: data/root each with total, free, additional bytes")
    p = commands.add_parser("generate")
    p.add_argument("campaign")
    p.add_argument("name")
    p.add_argument("--suite", choices=["tpch", "tpcds"], default="tpch")
    add_settings(p)
    add_sharding(p, target_default=None)
    p = commands.add_parser("shard-dataset")
    p.add_argument("campaign")
    p.add_argument("dataset")
    p.add_argument("name")
    add_sharding(p, target_default=268435456)
    p.add_argument("--timeout", type=positive_timeout, default=600)
    p.add_argument("--peak-bytes", type=positive_int)
    p = commands.add_parser("prepare-reference")
    p.add_argument("campaign")
    p.add_argument("dataset")
    p.add_argument("name")
    p = commands.add_parser("prepare-sql-reference")
    p.add_argument("campaign")
    p.add_argument("manifest")
    p.add_argument("name")
    p.add_argument("--timeout", type=float)
    p = commands.add_parser("freeze-public-connection")
    p.add_argument("output")
    add_adapter(p, public_only=True)
    p = commands.add_parser("qualify-packaged-tpch")
    p.add_argument("campaign")
    p.add_argument("dataset")
    p.add_argument("reference")
    p.add_argument("name")
    for command in ("prepare-ingest", "load-public", "freeze-snapshots", "prepare-tpch", "prepare-tpcds", "prepare-tpcds-supplement", "prepare-tpcds-approved"):
        p = commands.add_parser(command)
        p.add_argument("campaign")
        p.add_argument("input")
        p.add_argument("name")
        if command == "prepare-ingest":
            p.add_argument("--prefix", required=True)
        elif command in ("load-public", "freeze-snapshots"):
            add_adapter(p, public_only=True)
            if command == "load-public":
                p.add_argument("--batch-size", type=ingest_batch_size, default=2000)
        elif command in ("prepare-tpcds", "prepare-tpcds-supplement", "prepare-tpcds-approved"):
            p.add_argument("--timeout", type=positive_timeout, default=300)
            if command in ("prepare-tpcds-supplement", "prepare-tpcds-approved"):
                p.add_argument("--source-dir", required=True)
            if command == "prepare-tpcds-approved":
                p.add_argument("--spark-reference", required=True)
    commands.add_parser("tpcds-coverage")
    p = commands.add_parser("load-bulk")
    p.add_argument("campaign")
    p.add_argument("dataset")
    p.add_argument("name")
    p.add_argument("--prefix", required=True)
    p.add_argument("--bulk-auth-file", required=True)
    p.add_argument("--timeout", type=positive_timeout, default=600)
    p.add_argument("--peak-bytes", type=positive_int)
    add_adapter(p, public_only=True)
    p = commands.add_parser("verify-snapshots")
    p.add_argument("campaign")
    p.add_argument("pins")
    add_adapter(p, public_only=True)
    p = commands.add_parser("table-manifest")
    p.add_argument("pins")
    p.add_argument("output")
    for command in ("bind-tpch", "bind-tpcds"):
        p = commands.add_parser(command)
        p.add_argument("plan_manifest")
        p.add_argument("pins")
        p.add_argument("output")
    p = commands.add_parser("run")
    p.add_argument("campaign")
    p.add_argument("name")
    p.add_argument("manifest")
    p.add_argument("--references")
    p.add_argument("--reference-label")
    p.add_argument("--sql-variant", choices=["sql", "reference_sql"], default="sql")
    p.add_argument("--allow-arbitrary", action="store_true")
    p.add_argument("--abs-tol", type=float, default=0)
    p.add_argument("--rel-tol", type=float, default=0)
    add_adapter(p)
    add_settings(p)
    p = commands.add_parser("compare")
    p.add_argument("left")
    p.add_argument("right")
    p.add_argument("--ordered", action="store_true")
    p.add_argument("--abs-tol", type=float, default=0)
    p.add_argument("--rel-tol", type=float, default=0)
    p.add_argument("--campaign", required=True)
    p = commands.add_parser("inspect")
    p.add_argument("path", help="campaign/run/attempt JSON")
    p = commands.add_parser("reconcile")
    p.add_argument("attempt", help="existing owned attempt.json; never redispatch")
    p.add_argument("--rejection-evidence", help="explicit scoped local-operator audit JSON")
    add_adapter(p, trino_only=True)
    p = commands.add_parser("cleanup")
    p.add_argument("campaign")
    p.add_argument("name")
    args = parser.parse_args()
    try:
        cmd = args.command
        result = None
        if cmd == "protocol":
            result = {"protocol": protocol(), "resource_plan": plan(args.profile)}
        elif cmd == "init":
            result = Campaign.create(args.campaign, args.image, args.host_path, args.drain_bytes).state
        elif cmd == "preflight":
            result = Campaign(args.campaign).check(args.additional_bytes)
            result["guest_ready"] = "unknown; host free is not proof of guest capacity"
            if args.guest_snapshot:
                snapshot = json.loads(Path(args.guest_snapshot).read_text())
                result["guest"] = {k: guest_requirement(**snapshot[k], root=k == "root") for k in ("data", "root")}
                result["guest_ready"] = all(s["ready"] for s in result["guest"].values())
        elif cmd == "generate":
            options = config(args)
            if args.target_file_bytes is not None:
                options.update(target_file_bytes=args.target_file_bytes, row_group_size=args.row_group_size,
                               max_file_bytes=args.max_file_bytes)
            elif args.row_group_size != 122880 or args.max_file_bytes != 104857600000:
                raise ValueError("shard options require target-file-bytes")
            result = str(generate(Campaign(args.campaign), args.name, args.suite, **options))
        elif cmd == "shard-dataset":
            from .sharding import shard_dataset
            result = str(shard_dataset(Campaign(args.campaign), args.dataset, args.name,
                         target_file_bytes=args.target_file_bytes, row_group_size=args.row_group_size,
                         max_file_bytes=args.max_file_bytes, timeout=args.timeout, peak_bytes=args.peak_bytes))
        elif cmd == "prepare-reference":
            result = str(prepare_reference(Campaign(args.campaign), args.dataset, args.name))
        elif cmd == "prepare-sql-reference":
            result = str(prepare_sql_reference(Campaign(args.campaign), json.loads(Path(args.manifest).read_text()),
                                              args.name, timeout=args.timeout))
        elif cmd == "freeze-public-connection":
            target = Path(args.output).resolve()
            repo = Path(__file__).resolve().parents[2]
            if target == repo or repo in target.parents or target.exists():
                raise ValueError("fresh connection output outside Git required")
            public = adapter(args)
            result = public.identity()
            result["url"] = public.url
            result["auth_file"] = client_auth_path(args.auth_file, args.runtime_profile)
            write_json(target, result)
        elif cmd == "qualify-packaged-tpch":
            result = str(qualify_packaged_tpch(Campaign(args.campaign), args.dataset, args.reference, args.name))
        elif cmd == "prepare-ingest":
            from .ingest import prepare_ingest
            result = str(prepare_ingest(Campaign(args.campaign), args.input, args.name, args.prefix))
        elif cmd == "load-public":
            from .ingest import load_public
            result = str(load_public(Campaign(args.campaign), args.input, args.name, adapter(args),
                                     batch_size=args.batch_size))
        elif cmd == "load-bulk":
            from .bulk import load_bulk
            result = str(load_bulk(Campaign(args.campaign), args.dataset, args.name, args.prefix,
                                   adapter(args), args.bulk_auth_file, timeout=args.timeout, peak_bytes=args.peak_bytes))
        elif cmd == "freeze-snapshots":
            from .snapshots import freeze_snapshots
            result = str(freeze_snapshots(Campaign(args.campaign), args.input, args.name, adapter(args)))
        elif cmd == "verify-snapshots":
            from .snapshots import verify_snapshots
            result = str(verify_snapshots(Campaign(args.campaign), args.pins, adapter(args)))
        elif cmd == "tpcds-coverage":
            from .tpcds_variants import coverage
            result = coverage()
        elif cmd in ("prepare-tpch", "prepare-tpcds", "prepare-tpcds-supplement", "prepare-tpcds-approved"):
            if cmd == "prepare-tpcds-approved":
                from .tpcds_approved_variants import prepare_approved as prepare
            elif cmd == "prepare-tpcds-supplement":
                from .tpcds_variants import prepare_supplement as prepare
            elif cmd == "prepare-tpcds":
                from .tpcds_sql import prepare_tpcds as prepare
            else:
                from .tpch_sql import prepare_tpch as prepare
            options = {"timeout": args.timeout} if cmd in ("prepare-tpcds", "prepare-tpcds-supplement", "prepare-tpcds-approved") else {}
            if cmd in ("prepare-tpcds-supplement", "prepare-tpcds-approved"):
                options["source_dir"] = args.source_dir
            if cmd == "prepare-tpcds-approved":
                options["spark_reference"] = args.spark_reference
            result = str(prepare(Campaign(args.campaign), args.input, args.name, **options))
        elif cmd in ("bind-tpch", "bind-tpcds", "table-manifest"):
            from .snapshots import table_manifest
            target = Path(args.output).resolve()
            repo = Path(__file__).resolve().parents[2]
            if target == repo or repo in target.parents or target.exists():
                raise ValueError("fresh manifest output outside Git required")
            if cmd != "table-manifest":
                if cmd == "bind-tpcds":
                    from .tpcds_sql import bind_snapshots
                else:
                    from .tpch_sql import bind_snapshots
                result = bind_snapshots(json.loads(Path(args.plan_manifest).read_text()), json.loads(Path(args.pins).read_text()))
            else:
                result = table_manifest(json.loads(Path(args.pins).read_text()))
            # Exclusive creation; never overwrite another binding.
            with target.open("x", encoding="utf8") as f:
                json.dump(result, f, ensure_ascii=False, indent=2, allow_nan=False)
                f.write("\n")
        elif cmd == "run":
            manifest = json.loads(Path(args.manifest).read_text())
            result = str(run(Campaign(args.campaign), args.name, manifest, adapter(args),
                             references=args.references, reference_label=args.reference_label, sql_variant=args.sql_variant, allow_arbitrary=args.allow_arbitrary,
                             abs_tol=args.abs_tol, rel_tol=args.rel_tol, **config(args)))
        elif cmd == "compare":
            campaign = Campaign(args.campaign)
            scratch = campaign.own("comparison-" + __import__("uuid").uuid4().hex, "comparison")
            try:
                result = compare(args.left, args.right, ordered=args.ordered, abs_tol=args.abs_tol,
                                 rel_tol=args.rel_tol, scratch=scratch, check=campaign.check)
                campaign.mark(scratch, "SUCCESS")
            except BaseException:
                campaign.mark(scratch, "FAILED")
                raise
        elif cmd == "inspect":
            result = json.loads(Path(args.path).read_text())
        elif cmd == "reconcile":
            result = reconcile(args.attempt, adapter(args), rejection_evidence=args.rejection_evidence)
        elif cmd == "cleanup":
            Campaign(args.campaign).cleanup(args.name)
            result = {"cleaned": args.name, "baseline_preserved": True}
        print(json.dumps(result, indent=2, allow_nan=False))
    except Exception as exc:
        # No backend exception strings: these can contain Authorization or SQL data.
        parser.exit(1, "local-perf failed: " + type(exc).__name__ + "; inspect owned metadata\n")


def ingest_batch_size(value):
    size = int(value)
    if not 1 <= size <= 10000:
        raise argparse.ArgumentTypeError("batch size must be an integer from 1 to 10000")
    return size


def positive_int(value):
    number = int(value)
    if number <= 0:
        raise argparse.ArgumentTypeError("value must be a positive integer")
    return number


def positive_timeout(value):
    timeout = float(value)
    if not math.isfinite(timeout) or timeout <= 0:
        raise argparse.ArgumentTypeError("timeout must be a finite positive number")
    return timeout


def add_sharding(p, target_default):
    p.add_argument("--target-file-bytes", type=positive_int, default=target_default)
    p.add_argument("--row-group-size", type=positive_int, default=122880)
    p.add_argument("--max-file-bytes", type=positive_int, default=104857600000)


def add_settings(p):
    p.add_argument("--mode", choices=["qualification", "performance"], default="qualification")
    p.add_argument("--scale", type=float)
    p.add_argument("--peak", type=int, help="additional peak bytes including DB/copy/export/spill/sort/results")
    p.add_argument("--peak-evidence", help="estimate/breakdown and measured pilot evidence; required below default peak")
    p.add_argument("--timeout", type=float)
    p.add_argument("--repetitions", type=int)


def config(args):
    return dict(mode=args.mode, scale=args.scale, peak=args.peak,
                timeout=args.timeout, repetitions=args.repetitions, peak_evidence=args.peak_evidence)


def add_adapter(p, trino_only=False, public_only=False):
    p.add_argument("--adapter", choices=["public"] if public_only else ["trino", "public"] if trino_only else ["duckdb", "trino", "public"], required=True)
    p.add_argument("--dataset", help="successful local dataset for isolated DuckDB")
    p.add_argument("--url")
    p.add_argument("--user")
    p.add_argument("--auth-file", help="explicit mode 0600 client JSON")
    p.add_argument("--ca-file", help="verified direct Trino TLS CA")
    p.add_argument("--runtime-profile", help="public client_auth_file reference only")
    p.add_argument("--context-file", help="full captured nine-field public context JSON")
    p.add_argument("--connection-file", help="frozen public connection and credential reference")
    p.add_argument("--database", help="explicit public database id or SQL catalog")
    limits = p.add_mutually_exclusive_group()
    limits.add_argument("--max-rows", type=int)
    limits.add_argument("--all-results", action="store_true")
    p.add_argument("--sql-only", action="store_true", help="DuckDB without registered tables")
    p.add_argument("--password-env", help="environment variable name; never a credential argument")
    p.add_argument("--catalog")
    p.add_argument("--schema")


def adapter(args):
    all_results = getattr(args, "all_results", False)
    if all_results and args.adapter != "public":
        raise ValueError("all-results requires public adapter")
    if args.adapter == "duckdb":
        if bool(args.dataset) == bool(args.sql_only):
            raise ValueError("choose dataset or explicit SQL-only DuckDB")
        return DuckDBAdapter(args.dataset)
    if args.adapter == "public":
        if args.user or args.password_env or args.ca_file:
            raise ValueError("public uses explicit client auth reference")
        saved = json.loads(Path(args.connection_file).read_text()) if args.connection_file else None
        if saved and any((args.url, args.auth_file, args.runtime_profile, args.context_file, args.database, args.catalog, args.schema)):
            raise ValueError("saved connection cannot be overridden")
        if saved:
            if ("result_mode" in saved and saved["result_mode"] != "all"
                    or (saved.get("max_rows") is None) != (saved.get("result_mode") == "all")):
                raise ValueError("invalid saved result mode")
            if (all_results and saved.get("result_mode") != "all"
                    or args.max_rows is not None and args.max_rows != saved["max_rows"]):
                raise ValueError("saved result mode cannot be overridden")
        auth_path = saved["auth_file"] if saved else client_auth_path(args.auth_file, args.runtime_profile)
        auth = private_auth(auth_path, public=True)
        captured = saved["context"] if saved else json.loads(Path(args.context_file).read_text()) if args.context_file else None
        if captured is not None and any((args.database, args.catalog, args.schema)):
            raise ValueError("captured context cannot be overridden")
        max_rows = saved["max_rows"] if saved else None if all_results else args.max_rows if args.max_rows is not None else 10000
        public = PublicAdapter(saved["url"] if saved else args.url or "http://127.0.0.1:8888",
                               auth["username"], auth["password"], auth["selected_role"], captured, max_rows=max_rows)
        if captured is None:
            if bool(args.database) == bool(args.catalog) or not args.schema:
                raise ValueError("explicit database or catalog and schema required")
            public.select(args.database or args.catalog, args.schema)
        if saved and any(saved.get(k) != public.identity()[k] for k in ("kind", "origin", "account", "selected_role", "context", "max_rows")):
            raise ValueError("saved public identity changed")
        return public
    if args.runtime_profile or args.connection_file or args.context_file or args.database:
        raise ValueError("direct Trino cannot use public connection references")
    if args.auth_file:
        if args.user or args.password_env:
            raise ValueError("ambiguous direct credentials")
        auth = private_auth(args.auth_file)
        user, password = auth["username"], auth["password"]
    else:
        user, password = args.user or "local-perf", os.environ[args.password_env] if args.password_env else None
    return TrinoAdapter(args.url or "http://127.0.0.1:8080", user, password,
                        args.catalog, args.schema, ca_file=args.ca_file)


if __name__ == "__main__":
    main()
