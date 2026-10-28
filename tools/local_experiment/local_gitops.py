"""Local-only, content-addressed Flux Bucket delivery. No runtime work on import."""

import argparse
import base64
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import socket
import subprocess
import sys
import time

import yaml

CONTEXT = "colima-mac-studio-solo"
SOURCE = "shirokuma-local-source"
BUCKET = "shirokuma-gitops"
ROOT = Path(__file__).resolve().parents[2]
ALLOWED = ("deploy/gitops", "deploy/catalog/bootstrap", "deploy/local-experiment/trino")
CHILD_ROOT = "deploy/gitops/clusters/local-lite"
CHILD_FILES = ("dev", "object-storage", "catalog-database", "catalog-bootstrap", "catalog", "iceberg-bootstrap")
GENERATION_KEYS = ("POLARIS_CREDENTIAL_GENERATION", "POLARIS_SALES_JWT_KEY_GENERATION",
                   "POLARIS_FINANCE_JWT_KEY_GENERATION")
FORBIDDEN = {"var", ".git", "env", "secrets", "secret", ".env", ".venv", "__pycache__"}


def checked_path(path):
    """Only repository-relative files under the three fixed delivery roots."""
    p = PurePosixPath(path)
    if (p.is_absolute() or ".." in p.parts or
            any(part.lower() in FORBIDDEN or part.startswith(".") for part in p.parts) or
            not any(p.is_relative_to(base) for base in ALLOWED)):
        raise ValueError("path outside delivery allowlist")
    return p.as_posix()


def validate_content(path, content):
    text = content.decode("utf-8")
    if re.search(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY(?: BLOCK)?-----|PuTTY-User-Key-File-", text):
        raise ValueError("private key candidate rejected")
    if path.endswith((".yaml", ".yml", ".json")) or PurePosixPath(path).name == "Kustomization":
        def inspect(value):
            if isinstance(value, dict):
                if value.get("kind") == "Secret" or value.get("secretGenerator"):
                    raise ValueError("Kubernetes Secret candidate rejected")
                for item in value.values():
                    inspect(item)
            elif isinstance(value, list):
                for item in value:
                    inspect(item)
        for document in yaml.safe_load_all(text):
            inspect(document)


def validate_references(files):
    """Fail rather than publish a Kustomize reference outside the snapshot."""
    def reference(base, value):
        if not isinstance(value, str) or "://" in value or "::" in value:
            raise ValueError("non-local Kustomize reference")
        # Resolve ordinary ../ references before applying the fixed allowlist.
        from posixpath import normpath
        path = checked_path(normpath(str(PurePosixPath(base).parent / value)))
        if path not in files and not any(
                f"{path}/{name}" in files for name in ("kustomization.yaml", "kustomization.yml", "Kustomization")):
            raise ValueError("missing Kustomize reference")

    for path, content in files.items():
        if PurePosixPath(path).name not in ("kustomization.yaml", "kustomization.yml", "Kustomization"):
            continue
        doc = yaml.safe_load(content)
        for field in ("resources", "bases", "components", "configurations", "crds", "generators", "transformers", "patchesStrategicMerge"):
            for value in doc.get(field, []):
                if isinstance(value, str) and "\n" in value and field == "patchesStrategicMerge":
                    validate_content("inline.yaml", value.encode())
                else:
                    reference(path, value)
        for field in ("patches", "patchesJson6902"):
            for value in doc.get(field, []):
                if isinstance(value, str):
                    reference(path, value)
                elif "path" in value:
                    reference(path, value["path"])
                elif "patch" in value:
                    validate_content("inline.yaml", value["patch"].encode())
        for generator in doc.get("configMapGenerator", []):
            for value in generator.get("files", []) + generator.get("envs", []):
                reference(path, value.split("=", 1)[-1])
            if generator.get("env"):
                reference(path, generator["env"])
        if doc.get("openapi", {}).get("path"):
            reference(path, doc["openapi"]["path"])


def collect_files(root):
    root = root.resolve()
    files = {}
    for base in ALLOWED:
        if not (root / base).is_dir():
            raise ValueError("missing delivery root")
        for path in sorted((root / base).rglob("*")):
            relative = path.relative_to(root).as_posix()
            if path.is_symlink():
                raise ValueError("symlink in delivery tree")
            if not path.is_file():
                continue
            checked_path(relative)
            content = path.read_bytes()
            validate_content(relative, content)
            files[relative] = content
    validate_references(files)
    return files


def content_digest(files):
    digest = hashlib.sha256()
    for path, content in sorted(files.items()):
        digest.update(path.encode() + b"\0" + str(len(content)).encode() + b"\0" + content)
    return digest.hexdigest()


def generation_prefix(digest):
    if not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise ValueError("invalid content digest")
    return f"generations/{digest}/"


def child_definitions(files, prefix, suspend=False):
    """Render the local child map; never render/re-read the old root overlay."""
    data = yaml.safe_load(files[f"{CHILD_ROOT}/polaris-runtime-generation.yaml"])["data"]
    generations = {}
    for key in GENERATION_KEYS:
        if key not in data:
            continue
        value = data[key]
        if (isinstance(value, bool) or not isinstance(value, (str, int)) or
                not str(value).strip() or "__REPLACED" in str(value)):
            raise ValueError(f"invalid Polaris runtime generation: {key}")
        generations[key] = str(value)
    if GENERATION_KEYS[0] not in generations:
        raise ValueError("missing Polaris runtime generation")
    children = []
    for filename in CHILD_FILES:
        doc = yaml.safe_load(files[f"{CHILD_ROOT}/{filename}.yaml"])
        if (doc.get("kind") != "Kustomization" or
                doc["metadata"]["name"] != f"shirokuma-{filename}" or
                doc["metadata"].get("namespace") != "flux-system"):
            raise ValueError("unexpected child definition")
        spec = doc["spec"]
        path = checked_path(spec["path"].removeprefix("./"))
        spec["path"] = f"./{prefix}{path}"
        spec["sourceRef"] = {"kind": "Bucket", "name": SOURCE}
        spec["suspend"] = suspend
        if filename in ("catalog-database", "catalog-bootstrap", "catalog", "iceberg-bootstrap"):
            spec.setdefault("postBuild", {}).setdefault("substitute", {})[GENERATION_KEYS[0]] = generations[GENERATION_KEYS[0]]
        substitutions = spec.get("postBuild", {}).get("substitute", {})
        for key, value in substitutions.items():
            if key in GENERATION_KEYS or (isinstance(value, str) and "__REPLACED" in value):
                if key not in generations:
                    raise ValueError(f"missing Polaris runtime generation for substitution: {key}")
                substitutions[key] = generations[key]
        if filename == "object-storage":
            # Carry the lab's existing memory request and limit through reconciliation.
            spec.setdefault("patches", []).append({
                "target": {"group": "apps", "version": "v1", "kind": "StatefulSet", "name": "seaweedfs"},
                "patch": json.dumps({"apiVersion": "apps/v1", "kind": "StatefulSet", "metadata": {"name": "seaweedfs"},
                                     "spec": {"template": {"spec": {"containers": [{"name": "seaweedfs", "resources": {"requests": {"memory": "1Gi"}, "limits": {"memory": "8Gi"}}}]}}}}),
            })
        children.append(doc)
    children.append({"apiVersion": "kustomize.toolkit.fluxcd.io/v1", "kind": "Kustomization",
                     "metadata": {"name": "shirokuma-trino", "namespace": "flux-system"},
                     "spec": {"interval": "10m", "retryInterval": "1m", "timeout": "10m", "prune": True,
                              "suspend": suspend, "wait": True,
                              "sourceRef": {"kind": "Bucket", "name": SOURCE},
                              "path": f"./{prefix}deploy/local-experiment/trino/secure",
                              "dependsOn": [{"name": "shirokuma-iceberg-bootstrap"}],
                              "postBuild": {"substitute": {"POLARIS_CREDENTIAL_GENERATION": generations[GENERATION_KEYS[0]]}},
                              "healthChecks": [{"apiVersion": "apps/v1", "kind": "Deployment", "name": "trino-local", "namespace": "shirokuma-dev"}]}})
    return children


def source_definitions(root, prefix):
    base = root / "deploy/local-experiment/gitops"
    bucket = yaml.safe_load((base / "bucket.yaml").read_bytes())
    bucket["spec"]["prefix"] = prefix
    return [bucket, yaml.safe_load((base / "networkpolicy.yaml").read_bytes())]


def generation_children(files, child_files, prefix):
    """Retain generated child ownership; change only delivery location/source."""
    children = []
    for filename in child_files:
        checked_path(filename)
        doc = yaml.safe_load(files[filename])
        if (doc.get("apiVersion") != "kustomize.toolkit.fluxcd.io/v1" or
                doc.get("kind") != "Kustomization" or
                doc["metadata"].get("namespace") != "flux-system"):
            raise ValueError("unexpected generated child definition")
        spec = doc["spec"]
        path = checked_path(spec["path"].removeprefix("./"))
        if not any(f"{path}/{name}" in files for name in ("kustomization.yaml", "kustomization.yml", "Kustomization")):
            raise ValueError("missing generated child path")
        spec["path"] = f"./{prefix}{path}"
        spec["sourceRef"] = {"kind": "Bucket", "name": SOURCE}
        children.append(doc)
    return children


def validate_prospective_auth(path, manifest, identity):
    import runtime_profile
    from trino_auth import validate_database_config
    config = validate_database_config(runtime_profile.private_json(Path(path).resolve()))
    databases = config["databases"]
    if (set(databases) != set(manifest["spec"]["databases"]) or
            set(databases) != set(identity["databases"]) or
            config["platform_admin"]["username"] != identity["platform_admin"]):
        raise ValueError("prospective authentication identity mismatch")
    for key, entry in databases.items():
        expected = identity["databases"][key]
        if (entry["generation_id"] != manifest["generation_id"] or
                entry["generation_id"] != expected["generation_id"] or
                entry["sql_catalog"] != manifest["spec"]["databases"][key]["sql_catalog"] or
                entry["sql_catalog"] != expected["sql_catalog"] or
                {role: value["username"] for role, value in entry["roles"].items()} != expected["roles"]):
            raise ValueError("prospective authentication identity mismatch")


def prepare_database_generation(directory, prospective_auth=None):
    import database_generation as generation
    bundle = generation.load_bundle(directory, root=ROOT)
    manifest, public = bundle["manifest"], bundle["files"]
    files = {path: content for path, content in public.items() if path.startswith("deploy/")}
    for path, content in files.items():
        checked_path(path)
        validate_content(path, content)
    validate_references(files)
    digest = manifest["delivery_digest"]
    prefix = generation_prefix(digest)
    generation_children(files, manifest["child_files"], prefix)
    source_ref = manifest["spec"]["shared_secret_refs"]["gitops_source"]
    if (source_ref != bundle["tofu"]["shared"]["gitops_source_secret"] or
            source_ref["namespace"] != "flux-system" or
            set(source_ref["keys"]) != {"accesskey", "secretkey"}):
        raise ValueError("invalid generated source Secret reference")
    sources = source_definitions(ROOT, prefix)
    bucket = sources[0]
    if (bucket.get("kind") != "Bucket" or bucket["metadata"]["name"] != SOURCE or
            bucket["metadata"].get("namespace") != "flux-system"):
        raise ValueError("unexpected Bucket source definition")
    bucket["spec"]["secretRef"] = {"name": source_ref["name"]}
    for doc in sources:
        validate_content("source.yaml", yaml.safe_dump(doc).encode())
    if prospective_auth is not None:
        validate_prospective_auth(prospective_auth, manifest, json.loads(public["identity-public.json"]))
    return {"relativeFilesCount": len(files), "digest": digest, "generationId": manifest["generation_id"]}


def kubectl(*args, payload=None):
    result = subprocess.run(["rtk", "proxy", "kubectl", "--context", CONTEXT, *args],
                            input=payload, capture_output=True, text=True, timeout=120)
    if result.returncode:
        raise RuntimeError("local kubectl command failed (output withheld)")
    return result.stdout


def apply_documents(documents):
    kubectl("apply", "--server-side", "--force-conflicts", "--field-manager=shirokuma-local-gitops", "-f", "-",
            payload=yaml.safe_dump_all(documents))


def require_suspended_root():
    root = json.loads(kubectl("-n", "flux-system", "get", "kustomization", "flux-system", "-o", "json"))
    if root.get("spec", {}).get("suspend") is not True:
        raise ValueError("root flux-system must already be suspended")


def operator_credentials():
    secret = json.loads(kubectl("-n", "shirokuma-storage", "get", "secret", "seaweedfs-s3-credentials", "-o", "json"))
    config = json.loads(base64.b64decode(secret["data"]["s3.json"], validate=True))
    operators = [item for item in config["identities"] if "Admin" in item.get("actions", [])]
    if len(operators) != 1 or not operators[0].get("credentials"):
        raise ValueError("expected one SeaweedFS operator identity")
    return operators[0]["credentials"][0]


@contextmanager
def s3_port_forward():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    process = subprocess.Popen(["rtk", "proxy", "kubectl", "--context", CONTEXT,
                                "-n", "shirokuma-storage", "port-forward", "--address=127.0.0.1",
                                "service/seaweedfs-s3", f"{port}:8333"],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        deadline = time.monotonic() + 20
        while True:
            if process.poll() is not None or time.monotonic() >= deadline:
                raise RuntimeError("local S3 port-forward unavailable")
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                    break
            except OSError:
                time.sleep(0.1)
        yield f"http://127.0.0.1:{port}"
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()


def upload_files(client, files, prefix):
    """Conditional writes keep generation keys immutable, including on retries."""
    from botocore.exceptions import ClientError
    for path, content in sorted(files.items()):
        key = prefix + path
        try:
            client.put_object(Bucket=BUCKET, Key=key, Body=content, IfNoneMatch="*")
        except ClientError as exc:
            if exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode") != 412:
                raise RuntimeError("snapshot upload failed (details withheld)") from None
            response = client.get_object(Bucket=BUCKET, Key=key)
            try:
                existing = response["Body"].read()
            finally:
                response["Body"].close()
            if existing != content:
                raise ValueError("immutable snapshot key differs")


def upload_public_generation(bundle, credentials):
    """Upload only; explicit operator credentials, no source/child mutation or fallback."""
    import boto3
    from botocore.config import Config
    import database_generation
    expected = database_generation.render(bundle['manifest']['spec'])
    if bundle != expected:
        raise ValueError('Invalid public generation')
    files = {name: data for name, data in bundle['files'].items() if name.startswith('deploy/')}
    for name, data in files.items():
        checked_path(name)
        validate_content(name, data)
    validate_references(files)
    client = boto3.client('s3', endpoint_url=credentials['endpoint_url'], region_name=credentials['region'],
        aws_access_key_id=credentials['access_key_id'], aws_secret_access_key=credentials['secret_access_key'],
        config=Config(signature_version='s3v4', retries={'max_attempts': 0}, s3={'addressing_style': 'path'}))
    try:
        upload_files(client, files, generation_prefix(bundle['manifest']['delivery_digest']))
    except Exception:
        raise RuntimeError('Public generation upload outcome unavailable (details withheld)') from None
    finally:
        client.close()


def observe_public_generation(bundle, credentials):
    """GET every expected content-addressed object; absence is never a no-dispatch proof."""
    import boto3
    from botocore.config import Config
    import database_generation
    if database_generation.render(bundle['manifest']['spec']) != bundle:
        raise ValueError('Invalid public generation')
    client = boto3.client('s3', endpoint_url=credentials['endpoint_url'], region_name=credentials['region'],
        aws_access_key_id=credentials['access_key_id'], aws_secret_access_key=credentials['secret_access_key'],
        config=Config(signature_version='s3v4', retries={'max_attempts': 0}, s3={'addressing_style': 'path'}))
    prefix = generation_prefix(bundle['manifest']['delivery_digest'])
    try:
        for name, content in sorted(bundle['files'].items()):
            if not name.startswith('deploy/'):
                continue
            response = client.get_object(Bucket=BUCKET, Key=prefix+name)
            try:
                if response['Body'].read() != content:
                    raise ValueError('Immutable public object mismatch')
            finally:
                response['Body'].close()
        return {'evidence_ref': 'snapshot-observed:'+bundle['manifest']['delivery_digest']}
    except Exception:
        raise RuntimeError('Public snapshot observation unavailable (details withheld)') from None
    finally:
        client.close()


def snapshot(root, files, prefix):
    try:
        import boto3
        from botocore.config import Config
    except ImportError:
        raise RuntimeError("snapshot requires optional boto3 dependency") from None
    require_suspended_root()
    credentials = operator_credentials()
    with s3_port_forward() as endpoint:
        # Explicit credentials and region prevent ambient profile/metadata lookup.
        client = boto3.client("s3", endpoint_url=endpoint, region_name="us-east-1",
                              aws_access_key_id=credentials["accessKey"],
                              aws_secret_access_key=credentials["secretKey"],
                              config=Config(signature_version="s3v4", s3={"addressing_style": "path"}))
        try:
            upload_files(client, files, prefix)
        finally:
            client.close()
    # Only a completely uploaded generation can become the Bucket source.
    apply_documents(source_definitions(root, prefix))


def activate(files, prefix, suspend=False):
    require_suspended_root()
    bucket = json.loads(kubectl("-n", "flux-system", "get", "bucket", SOURCE, "-o", "json"))
    if bucket["spec"].get("prefix") != prefix:
        raise ValueError("local content differs from the uploaded snapshot")
    ready = any(c.get("type") == "Ready" and c.get("status") == "True" and
                c.get("observedGeneration") == bucket["metadata"]["generation"]
                for c in bucket.get("status", {}).get("conditions", []))
    if not ready:
        raise ValueError("Bucket must be Ready for its current generation before activation")
    apply_documents(child_definitions(files, prefix, suspend))


def main(argv=None):
    # Keep sibling imports available for both script and python -m entrypoints.
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import runtime_profile
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("prepare", "snapshot", "activate", "status"))
    parser.add_argument("--suspend", action="store_true", help="activate all children suspended for controlled cutover")
    parser.add_argument("--database-generation", help="offline public generation bundle (prepare only)")
    parser.add_argument("--prospective-backend-auth", help="private v2 backend authentication for generation prepare")
    args = parser.parse_args(argv)
    try:
        if args.database_generation is not None:
            if args.command != "prepare":
                raise ValueError("database generations support prepare only")
            print(json.dumps(prepare_database_generation(args.database_generation, args.prospective_backend_auth), sort_keys=True))
            return 0
        if args.prospective_backend_auth is not None:
            raise ValueError("prospective authentication requires generation prepare")
        runtime_profile.configure_process()
        if args.command == "status":
            names = ["flux-system"] + [f"shirokuma-{name}" for name in CHILD_FILES] + ["shirokuma-trino"]
            # Resource tables only; no Secret reads or data dumps.
            print(kubectl("-n", "flux-system", "get", "bucket", SOURCE), end="")
            print(kubectl("-n", "flux-system", "get", "kustomization", *names), end="")
            return 0
        files = runtime_profile.auth_overlay(collect_files(ROOT))
        for path, content in files.items():
            checked_path(path)
            validate_content(path, content)
        validate_references(files)
        digest = content_digest(files)
        prefix = generation_prefix(digest)
        child_definitions(files, prefix, args.suspend)  # preflight before any runtime effect
        if args.command == "snapshot":
            snapshot(ROOT, files, prefix)
        elif args.command == "activate":
            activate(files, prefix, args.suspend)
        print(json.dumps({"relativeFilesCount": len(files), "digest": digest}, sort_keys=True))
        return 0
    except Exception:
        # Credential parsers, SDKs and kubectl exceptions can contain private values.
        print("local GitOps command failed; check local prerequisites and snapshot inputs", file=__import__("sys").stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
