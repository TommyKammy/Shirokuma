"""Explicit local commands; none apply VM/workload changes."""
import argparse
import json
import os
from pathlib import Path

from .capacity import Campaign, guest_requirement
from .dataset import generate, prepare_reference, qualify_packaged_tpch
from .protocol import GIB, plan, protocol
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
    p = commands.add_parser("prepare-reference")
    p.add_argument("campaign")
    p.add_argument("dataset")
    p.add_argument("name")
    p = commands.add_parser("qualify-packaged-tpch")
    p.add_argument("campaign")
    p.add_argument("dataset")
    p.add_argument("reference")
    p.add_argument("name")
    p = commands.add_parser("run")
    p.add_argument("campaign")
    p.add_argument("name")
    p.add_argument("manifest")
    p.add_argument("--references", required=True)
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
    p.add_argument("attempt", help="existing attempt.json, cancellation only; never redispatch")
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
            result = str(generate(Campaign(args.campaign), args.name, args.suite, **config(args)))
        elif cmd == "prepare-reference":
            result = str(prepare_reference(Campaign(args.campaign), args.dataset, args.name))
        elif cmd == "qualify-packaged-tpch":
            result = str(qualify_packaged_tpch(Campaign(args.campaign), args.dataset, args.reference, args.name))
        elif cmd == "run":
            manifest = json.loads(Path(args.manifest).read_text())
            result = str(run(Campaign(args.campaign), args.name, manifest, adapter(args),
                             references=args.references, allow_arbitrary=args.allow_arbitrary,
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
            result = reconcile(args.attempt, adapter(args))
        elif cmd == "cleanup":
            Campaign(args.campaign).cleanup(args.name)
            result = {"cleaned": args.name, "baseline_preserved": True}
        print(json.dumps(result, indent=2, allow_nan=False))
    except Exception as exc:
        # No backend exception strings: these can contain Authorization or SQL data.
        parser.exit(1, "local-perf failed: " + type(exc).__name__ + "; inspect owned metadata\n")


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


def add_adapter(p, trino_only=False):
    p.add_argument("--adapter", choices=["trino"] if trino_only else ["duckdb", "trino"], required=True)
    p.add_argument("--dataset", help="successful local dataset for isolated DuckDB")
    p.add_argument("--url", default="http://127.0.0.1:8080")
    p.add_argument("--user", default="local-perf")
    p.add_argument("--password-env", help="environment variable name; never a credential argument")
    p.add_argument("--catalog")
    p.add_argument("--schema")


def adapter(args):
    if args.adapter == "duckdb":
        if not args.dataset:
            raise ValueError("dataset required")
        return DuckDBAdapter(args.dataset)
    return TrinoAdapter(args.url, args.user, os.environ[args.password_env] if args.password_env else None,
                        args.catalog, args.schema)


if __name__ == "__main__":
    main()
