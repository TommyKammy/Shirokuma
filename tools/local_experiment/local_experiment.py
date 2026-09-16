#!/usr/bin/env python3
"""Apply existing local experiment resources without publishing a Git revision."""

from __future__ import annotations

import argparse
import datetime as dt
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from urllib.parse import urlsplit


ROOT = Path(__file__).resolve().parents[2]
CONTEXT = "colima-mac-studio-solo"
FLUX_KIND = "kustomization.kustomize.toolkit.fluxcd.io"
COMPONENTS = {
    "dev": ("deploy/gitops/dev", "shirokuma-dev", {
        ("v1", "ConfigMap", "shirokuma-dev", "repository-reconciliation-smoke"),
    }),
    "catalog": ("deploy/gitops/catalog/server", "shirokuma-catalog", {
        ("apps/v1", "Deployment", "shirokuma-dev", "polaris"),
        ("v1", "Service", "shirokuma-dev", "polaris"),
        ("networking.k8s.io/v1", "NetworkPolicy", "shirokuma-dev", "polaris"),
    }),
}


class ExperimentError(RuntimeError):
    pass


def run(args: list[str], text: str | None = None) -> str:
    try:
        result = subprocess.run(args, input=text, text=True, capture_output=True, timeout=180)
    except (OSError, subprocess.SubprocessError) as error:
        raise ExperimentError(f"{args[0]} command did not complete; inspect locally") from error
    if result.returncode:
        # Do not print raw server output: admission errors may include env values.
        raise ExperimentError(f"{args[0]} {args[1]} failed (exit {result.returncode}); inspect locally")
    return result.stdout


def kube(*args: str, text: str | None = None) -> str:
    return run(["kubectl", "--context", CONTEXT, "--request-timeout=60s", *args], text)


def get(kind: str, name: str, namespace: str) -> dict:
    return json.loads(kube("-n", namespace, "get", kind, name, "-o", "json"))


def check_context() -> None:
    config = json.loads(kube("config", "view", "--minify", "-o", "json"))
    servers = [x["cluster"]["server"] for x in config.get("clusters", [])]
    if len(servers) != 1:
        raise ExperimentError("expected one local Colima API server")
    server = urlsplit(servers[0])
    if server.scheme != "https" or server.hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise ExperimentError("refusing a non-loopback Kubernetes API server")
    nodes = json.loads(kube("get", "nodes", "-o", "json")).get("items", [])
    if len(nodes) != 1 or nodes[0].get("status", {}).get("nodeInfo", {}).get("architecture") != "arm64":
        raise ExperimentError("expected the single ARM64 Colima node")
    if "k3s" not in nodes[0]["status"]["nodeInfo"].get("kubeletVersion", ""):
        raise ExperimentError("expected Colima built-in k3s")


def json_documents(text: str) -> list[dict]:
    decoder = json.JSONDecoder()
    documents = []
    while text.strip():
        value, offset = decoder.raw_decode(text.lstrip())
        text = text.lstrip()[offset:]
        documents.extend(value["items"] if value.get("kind") == "List" else [value])
    return documents


def decode_yaml(text: str) -> list[dict]:
    return json_documents(kube("patch", "--local=true", "--type=merge", "-p", "{}", "-f", "-", "-o", "json", text=text))


def check_local_inputs(directory: Path, seen: set[Path] | None = None) -> None:
    """Reject remote bases/charts and references outside this checkout."""
    seen = set() if seen is None else seen
    directory = directory.resolve()
    if directory in seen:
        raise ExperimentError("cyclic local Kustomize references")
    seen.add(directory)
    path = directory / "kustomization.yaml"
    config = decode_yaml(path.read_text())[0]
    if any(key in config for key in ("helmCharts", "generators", "transformers")):
        raise ExperimentError("external charts/generators/transformers are outside this runner")
    refs = [x for key in ("resources", "bases", "components", "crds") for x in config.get(key, [])]
    for patch in config.get("patches", []):
        if isinstance(patch, dict) and "path" in patch:
            refs.append(patch["path"])
    refs.extend(config.get("patchesStrategicMerge", []))
    for ref in refs:
        candidate = (directory / ref).resolve()
        if not candidate.is_relative_to(ROOT) or not candidate.exists():
            raise ExperimentError("manifest references must exist inside the local checkout")
        if candidate.is_dir():
            check_local_inputs(candidate, seen)
    seen.remove(directory)


def identity(obj: dict) -> tuple[str, str, str, str]:
    return (obj["apiVersion"], obj["kind"], obj["metadata"].get("namespace", ""), obj["metadata"]["name"])


def validate_objects(component: str, objects: list[dict]) -> None:
    identities = [identity(obj) for obj in objects]
    if len(identities) != len(set(identities)) or set(identities) != COMPONENTS[component][2]:
        raise ExperimentError("rendered resources differ from the fixed component allowlist")


def resource_kind(obj: dict) -> str:
    version = obj["apiVersion"]
    return obj["kind"] + ("." + version.split("/")[0] if "/" in version else "")


def resource_list(objects: list[dict]) -> str:
    return json.dumps({"apiVersion": "v1", "kind": "List", "items": objects}, indent=2) + "\n"


def desired_state(obj: dict) -> dict:
    """Ignore controller status churn when detecting edits since prepare."""
    value = {key: item for key, item in obj.items() if key not in {"metadata", "status"}}
    metadata = obj["metadata"]
    value["metadata"] = {key: metadata[key] for key in ("uid", "name", "namespace", "labels", "annotations") if key in metadata}
    return value


def write_json(path: Path, value: dict) -> None:
    temporary = path.with_suffix(".new")
    temporary.write_text(json.dumps(value, indent=2) + "\n")
    temporary.chmod(0o600)
    temporary.replace(path)


def digest(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def prepare(component: str) -> Path:
    check_context()
    path, reconciler, _ = COMPONENTS[component]
    check_local_inputs(ROOT / path)
    selected = get(FLUX_KIND, reconciler, "flux-system")
    rendered = kube("kustomize", str(ROOT / path))
    if component == "catalog":
        generation = selected.get("spec", {}).get("postBuild", {}).get("substitute", {}).get("POLARIS_CREDENTIAL_GENERATION", "")
        if not re.fullmatch(r"[1-9][0-9]*", str(generation)):
            raise ExperimentError("live Polaris credential generation is not a positive integer")
        rendered = rendered.replace("${POLARIS_CREDENTIAL_GENERATION}", str(generation))
    if "${" in rendered or "__REPLACED_BY_" in rendered:
        raise ExperimentError("unresolved manifest substitution")
    objects = decode_yaml(rendered)
    validate_objects(component, objects)
    before = [get(resource_kind(obj), obj["metadata"]["name"], obj["metadata"]["namespace"]) for obj in objects]
    # Existing objects only: no resource creation, Job reruns, PVC changes or deletion.
    payload = resource_list(objects)
    kube("apply", "--dry-run=server", "-f", "-", text=payload)
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    directory = ROOT / "tmp" / "local-experiment" / f"{stamp}-{component}"
    directory.mkdir(parents=True, mode=0o700)
    (directory / "rendered.json").write_text(payload)
    (directory / "before.json").write_text(resource_list(before))
    plan = {"version": 1, "context": CONTEXT, "component": component,
            "prepared_at": stamp, "git_head": run(["git", "-C", str(ROOT), "rev-parse", "HEAD"]).strip(),
            "rendered_sha256": digest(payload), "before_sha256": digest((directory / "before.json").read_text()),
            "status": "prepared", "suspensions": []}
    write_json(directory / "plan.json", plan)
    for file in directory.iterdir():
        file.chmod(0o600)
    return directory


def load_plan(directory: Path) -> dict:
    plan = json.loads((directory / "plan.json").read_text())
    if plan.get("version") != 1 or plan.get("context") != CONTEXT or plan.get("component") not in COMPONENTS:
        raise ExperimentError("unsupported experiment plan")
    for filename, field in (("rendered.json", "rendered_sha256"), ("before.json", "before_sha256")):
        if digest((directory / filename).read_text()) != plan[field]:
            raise ExperimentError(f"{filename} changed after prepare")
        validate_objects(plan["component"], json_documents((directory / filename).read_text()))
    return plan


def suspension_snapshot(name: str) -> dict:
    obj = get(FLUX_KIND, name, "flux-system")
    return {"name": name, "uid": obj["metadata"]["uid"],
            "previous": obj.get("spec", {}).get("suspend"), "changed": False, "pending": False}


def set_suspended(entry: dict, value: bool | None, expected: bool | None) -> None:
    current = get(FLUX_KIND, entry["name"], "flux-system")
    if current["metadata"]["uid"] != entry["uid"]:
        raise ExperimentError("Flux resource was recreated; refusing to alter its suspension")
    if current.get("spec", {}).get("suspend") != expected:
        raise ExperimentError("Flux suspension changed concurrently; refusing to overwrite it")
    patch = {"metadata": {"resourceVersion": current["metadata"]["resourceVersion"]}, "spec": {"suspend": value}}
    kube("-n", "flux-system", "patch", FLUX_KIND, entry["name"], "--type=merge", "-p", json.dumps(patch))


def patch_existing(desired: dict, before: dict) -> None:
    """Dry-run merges/defaults, then PATCH atomically against the observed object."""
    live = get(resource_kind(before), before["metadata"]["name"], before["metadata"]["namespace"])
    if desired_state(live) != desired_state(before):
        raise ExperimentError("resource changed after prepare; prepare again")
    merged = json.loads(kube("apply", "--dry-run=server", "-f", "-", "-o", "json", text=json.dumps(desired)))
    if any(merged["metadata"].get(key) != live["metadata"][key] for key in ("uid", "resourceVersion")):
        raise ExperimentError("resource changed during apply dry-run; prepare again")
    patch = [{"op": "test", "path": "/metadata/uid", "value": live["metadata"]["uid"]},
             {"op": "test", "path": "/metadata/resourceVersion", "value": live["metadata"]["resourceVersion"]},
             {"op": "replace", "path": "", "value": merged}]
    kube("-n", before["metadata"]["namespace"], "patch", resource_kind(before), before["metadata"]["name"],
         "--type=json", "-p", json.dumps(patch))


def apply(directory: Path, inherited: dict | None = None) -> None:
    plan = load_plan(directory)
    if plan["status"] != "prepared":
        raise ExperimentError("prepare a new run; this plan has already been applied or attempted")
    check_context()
    before = json_documents((directory / "before.json").read_text())
    for obj in before:
        live = get(resource_kind(obj), obj["metadata"]["name"], obj["metadata"]["namespace"])
        if desired_state(live) != desired_state(obj):
            raise ExperimentError("resource changed after prepare; prepare again")
    selected = COMPONENTS[plan["component"]][1]
    plan["suspensions"] = (json.loads(json.dumps(inherited["suspensions"])) if inherited else
                           [suspension_snapshot(name) for name in ("flux-system", selected)])
    if [entry["name"] for entry in plan["suspensions"]] != ["flux-system", selected]:
        raise ExperimentError("unexpected inherited suspension scope")
    if any(entry.get("pending") for entry in plan["suspensions"]):
        raise ExperimentError("ambiguous prior suspension; inspect the previous run before continuing")
    plan["status"] = "applying"
    write_json(directory / "plan.json", plan)
    try:
        for entry in plan["suspensions"]:
            if entry["previous"] is True or entry["changed"]:
                current = get(FLUX_KIND, entry["name"], "flux-system")
                if current["metadata"]["uid"] != entry["uid"] or current.get("spec", {}).get("suspend") is not True:
                    raise ExperimentError("previously suspended Flux resource changed; inspect before continuing")
            if entry["previous"] is not True and not entry["changed"]:
                # Distinguish an interrupted request from a confirmed mutation.
                entry["pending"] = True
                write_json(directory / "plan.json", plan)
                set_suspended(entry, True, entry["previous"])
                entry.update(changed=True, pending=False)
                write_json(directory / "plan.json", plan)
        for obj, original in zip(json_documents((directory / "rendered.json").read_text()), before):
            if identity(obj) != identity(original):
                raise ExperimentError("resource order changed in evidence")
            patch_existing(obj, original)
        plan["status"] = "applied"
    except (ExperimentError, subprocess.SubprocessError, OSError):
        plan["status"] = "apply-failed"
        raise
    finally:
        write_json(directory / "plan.json", plan)


def resume(directory: Path) -> None:
    plan = load_plan(directory)
    if plan["status"] not in {"applied", "apply-failed", "applying", "resume-failed"}:
        raise ExperimentError("this plan has no active suspension to restore")
    check_context()
    expected = ["flux-system", COMPONENTS[plan["component"]][1]]
    if [x["name"] for x in plan["suspensions"]] != expected:
        raise ExperimentError("unexpected suspension scope")
    if any(entry.get("pending") for entry in plan["suspensions"]):
        raise ExperimentError("ambiguous suspension request; inspect cluster state before recovery")
    try:
        for entry in reversed(plan["suspensions"]):
            if entry["changed"]:
                set_suspended(entry, entry["previous"], True)
                entry["changed"] = False
                write_json(directory / "plan.json", plan)
        plan["status"] = "resumed"
    except (ExperimentError, subprocess.SubprocessError, OSError):
        plan["status"] = "resume-failed"
        raise
    finally:
        write_json(directory / "plan.json", plan)


def status() -> None:
    check_context()
    reconcilers = json.loads(kube("-n", "flux-system", "get", FLUX_KIND, "-o", "json"))["items"]
    print(f"context: {CONTEXT}")
    for obj in reconcilers:
        conditions = obj.get("status", {}).get("conditions", [])
        ready = next((x["status"] for x in conditions if x["type"] == "Ready"), "Unknown")
        print(f"Flux {obj['metadata']['name']}: suspended={obj['spec'].get('suspend', False)} ready={ready}")
    for namespace in ("shirokuma-dev", "shirokuma-storage"):
        pods = json.loads(kube("-n", namespace, "get", "pods", "-o", "json"))["items"]
        for obj in pods:
            containers = obj.get("status", {}).get("containerStatuses", [])
            ready = sum(bool(x.get("ready")) for x in containers)
            restarts = sum(x.get("restartCount", 0) for x in containers)
            print(f"Pod {namespace}/{obj['metadata']['name']}: {obj['status'].get('phase')} ready={ready}/{len(containers)} restarts={restarts}")


def active_run(path: Path) -> dict | None:
    active = json.loads(path.read_text()) if path.exists() else None
    if active and active.get("previous_run"):
        current = load_plan(Path(active["run"]))
        if current["status"] == "prepared":
            # The process may have died after reserving the next run, before
            # persisting inherited recovery state. Its predecessor still owns
            # every suspension, so restore that durable recovery pointer.
            active = {"run": active["previous_run"]}
            write_json(path, active)
    return active


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("status")
    sub.add_parser("prepare").add_argument("component", choices=COMPONENTS)
    for command in ("apply", "resume"):
        sub.add_parser(command).add_argument("run", type=Path)
    args = parser.parse_args()
    os.umask(0o077)
    try:
        # Common Git directory serializes commands across this repo's worktrees.
        common = Path(run(["git", "-C", str(ROOT), "rev-parse", "--path-format=absolute", "--git-common-dir"]).strip())
        with (common / "shirokuma-local-experiment.lock").open("w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            if args.command == "status":
                status()
            elif args.command == "prepare":
                print(prepare(args.component))
            else:
                directory = args.run.resolve()
                plan = load_plan(directory)
                active_path = common / "shirokuma-local-experiment-active.json"
                active = active_run(active_path)
                inherited = None
                if active and active["run"] != str(directory):
                    inherited = load_plan(Path(active["run"]))
                    if (args.command != "apply" or inherited["component"] != plan["component"] or
                            inherited["status"] not in {"applied", "apply-failed"}):
                        raise ExperimentError(f"another run retains suspensions: {active['run']}")
                if args.command == "apply":
                    if plan["status"] != "prepared":
                        raise ExperimentError("prepare a new run; this plan has already been applied or attempted")
                    # Reserve the run before its first mutation. A crashed process
                    # leaves a pointer to the recovery plan, across worktrees.
                    reservation = {"run": str(directory)}
                    if inherited:
                        reservation["previous_run"] = active["run"]
                    write_json(active_path, reservation)
                try:
                    if args.command == "apply":
                        apply(directory, inherited)
                    else:
                        resume(directory)
                finally:
                    plan = load_plan(directory)
                    if plan["status"] in {"prepared", "resumed"}:
                        if inherited and plan["status"] == "prepared":
                            write_json(active_path, active)
                        else:
                            active_path.unlink(missing_ok=True)
                    elif inherited:
                        inherited.update(status="superseded", superseded_by=str(directory))
                        write_json(Path(active["run"]) / "plan.json", inherited)
                print(f"{args.command} complete: {directory}")
        return 0
    except (ExperimentError, OSError, ValueError, KeyError, subprocess.SubprocessError) as error:
        print(f"local experiment: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
