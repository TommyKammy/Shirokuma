"""Bounded severity metadata for the enrolled Foundation trino-opa container."""
from datetime import datetime, timezone
import re
import time

import db_platform_loki_errors as severity
from db_platform_foundation_baseline import _foundation
from db_platform_observations import SOURCE_ERRORS
import db_platform_usage as kube
import logical_foundation as lf
from warehouse_operations import OperationError

PATH = "/api/v1/bears/platform-loki-foundation-errors"
_SOURCE = "trino-opa"
_CONTAINER = "opa"
_IMAGE = "docker.io/openpolicyagent/opa@sha256:2ce2bacc87ddc25a475d71b67d422d206c20cf85b6ed00d81d520a5a039e22ce"
_CANONICAL_IMAGE = _IMAGE.removeprefix("docker.io/")
_APP = "app.kubernetes.io/name"
_HASH = "pod-template-hash"
_ROLLOUT = "deployment.kubernetes.io/revision"
_LIMIT = 65536
_TAIL = 100
_STAMP = re.compile(
    r"([0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2})"
    r"(?:\.([0-9]{1,9}))?(Z|[+-](?:[01][0-9]|2[0-3]):[0-5][0-9])"
)


def _timestamp(value):
    if not isinstance(value, str) or not (match := _STAMP.fullmatch(value)):
        raise ValueError()
    whole, fraction, offset = match.groups()
    stamp = datetime.fromisoformat(whole + ("+00:00" if offset == "Z" else offset))
    delta = stamp - datetime(1970, 1, 1, tzinfo=timezone.utc)
    return (delta.days * 86400 + delta.seconds) * severity._SECOND + int((fraction or "").ljust(9, "0"))


def _events(raw, window, started):
    if len(raw) > _LIMIT or (raw and not raw.endswith(b"\n")) or raw.count(b"\n") > _TAIL:
        raise ValueError()
    result = set()
    for line in raw.split(b"\n")[:-1]:
        prefix, sep, payload = line.partition(b" ")
        if not sep:
            raise ValueError()
        stamp = _timestamp(prefix.decode("ascii"))
        if not max(window[0], started) <= stamp <= window[1]:
            continue
        try:
            entry = kube._decode_json(payload.decode("utf-8"))
        except (ValueError, RecursionError):
            continue
        if isinstance(entry, dict) and entry.get("level") in severity._LEVELS:
            result.add((stamp, _SOURCE, entry["level"]))
    return result


def _labels(foundation):
    namespace = foundation["kube"]["namespace"]
    foundation_id = foundation["foundation_id"]
    environment_id = foundation["environment_id"]
    revision = str(foundation["revision"])
    if (namespace == "shirokuma-dev" or not lf.DNS.fullmatch(namespace)
            or not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9_.-]{0,61}[A-Za-z0-9])?", foundation_id)
            or not lf.ID.fullmatch(environment_id)
            or not re.fullmatch(r"[1-9][0-9]{0,62}", revision)
            or foundation["images"]["opa"] != _IMAGE):
        raise ValueError()
    return dict(service_name="shirokuma-local", component="sre-foundation-severity",
                source=_SOURCE, namespace=namespace,
                foundation_id=foundation_id, foundation_revision=revision,
                environment_id=environment_id)


def _template(obj, foundation):
    template = obj["spec"]["template"]
    labels = template["metadata"]["labels"]
    kube._labels(labels, foundation)
    if labels.get(_APP) != _SOURCE:
        raise ValueError()
    _container(template)
    return labels


def _container(obj):
    containers = obj["spec"]["containers"]
    if kube._containers(obj) != [_CONTAINER] or containers[0]["image"] != _IMAGE:
        raise ValueError()


def _object(obj, kind, name, namespace, foundation):
    api = "v1" if kind == "Pod" else "apps/v1"
    identity = kube._meta(obj, namespace)
    if obj["kind"] != kind or obj["apiVersion"] != api or identity[0] != name:
        raise ValueError()
    kube._labels(obj["metadata"]["labels"], foundation)
    return identity


def _identity(argv, foundation, deadline):
    namespace = foundation["kube"]["namespace"]

    def get(*args):
        return kube._read(argv + ["get", *args, "-o", "json"], deadline)

    deployment = get("deployment/" + _SOURCE)
    dep = _object(deployment, "Deployment", _SOURCE, namespace, foundation)
    _template(deployment, foundation)
    dep_meta = deployment["metadata"]
    rollout = dep_meta["annotations"][_ROLLOUT]
    generation = dep_meta["generation"]
    if (deployment["spec"]["selector"] != {"matchLabels": {_APP: _SOURCE}}
            or deployment["spec"]["replicas"] != 1
            or type(generation) is not int or generation < 1
            or deployment["status"]["observedGeneration"] != generation
            or not re.fullmatch(r"[1-9][0-9]*", rollout)):
        raise ValueError()

    pods = get("pods", "-l", _APP + "=" + _SOURCE)
    if pods["kind"] not in ("List", "PodList"):
        raise ValueError()
    pod, = kube._items(pods, limit=1)
    pod_name = pod["metadata"]["name"]
    rs_name, rs_uid = kube._owner(pod, "ReplicaSet")
    if not lf.DNS.fullmatch(pod_name) or not lf.DNS.fullmatch(rs_name):
        raise ValueError()
    pod_id = _object(pod, "Pod", pod_name, namespace, foundation)
    pod_labels = pod["metadata"]["labels"]
    _container(pod)
    rs_obj = get("replicaset/" + rs_name)
    rs = _object(rs_obj, "ReplicaSet", rs_name, namespace, foundation)
    rs_meta = rs_obj["metadata"]
    rs_labels = _template(rs_obj, foundation)
    pod_hash = pod_labels[_HASH]
    if (pod_labels.get(_APP) != _SOURCE or rs_labels.get(_HASH) != pod_hash
            or not re.fullmatch(r"[a-z0-9]{1,63}", pod_hash)
            or rs_obj["spec"]["selector"] != {"matchLabels": {_APP: _SOURCE, _HASH: pod_hash}}
            or rs_meta["labels"].get(_APP) != _SOURCE or rs_meta["labels"].get(_HASH) != pod_hash
            or rs[1] != rs_uid or kube._owner(rs_obj, "Deployment") != dep[:2]
            or rs_meta["annotations"][_ROLLOUT] != rollout or rs_obj["spec"]["replicas"] != 1):
        raise ValueError()

    status, = pod["status"]["containerStatuses"]
    state = status["state"]
    container_id = kube._text(status["containerID"])
    image, image_id = status["image"], status["imageID"]
    restarts = status["restartCount"]
    if (pod["status"]["phase"] != "Running" or status["name"] != _CONTAINER
            or image not in (_IMAGE, _CANONICAL_IMAGE)
            or image_id not in (_IMAGE, "docker-pullable://" + _CANONICAL_IMAGE)
            or set(state) != {"running"}
            or not re.fullmatch(r"(?:docker|containerd)://[0-9a-f]{64}", container_id)
            or type(restarts) is not int or restarts < 0):
        raise ValueError()
    started = _timestamp(state["running"]["startedAt"])
    if started < _timestamp(pod_id[2]):
        raise ValueError()
    return dep, generation, rollout, rs, pod_id, container_id, image, image_id, restarts, started


def _collect(foundation, check):
    argv = lf.kube_argv(foundation)
    deadline = time.monotonic() + 10
    check()
    before = _identity(argv, foundation, deadline)
    check()
    raw = kube._read_bytes(argv + ["logs", "pod/" + before[4][0], "-c", _CONTAINER,
                          "--timestamps=true", "--since=10m", "--tail=100",
                          "--limit-bytes=65537"], deadline, _LIMIT)
    check()
    if _identity(argv, foundation, deadline) != before:
        raise ValueError()
    check()
    window = severity._window()
    return window, dict(status="available", events=_events(raw, window, before[-1]))


def _enrollment(authority):
    try:
        return _foundation(authority)
    except SOURCE_ERRORS as error:
        if isinstance(error, OperationError) and error.code == "PLATFORM_ACCESS_DENIED":
            raise
        raise ValueError() from None


def _operate(principal, role, capture):
    authority = severity.logs._authority_check(principal, role)
    window = severity._window()
    missing = {_SOURCE: dict(status="unavailable", events=())}
    result = None
    try:
        foundation = _enrollment(authority)
        labels = _labels(foundation)
        selector = "{" + ",".join(f'{key}="{value}"' for key, value in labels.items()) + "}"

        def check():
            if _enrollment(authority) != foundation:
                raise ValueError()

        scope = dict(selector=selector, labels=labels, sources=(_SOURCE,))
        if capture:
            window, sample = _collect(foundation, check)
            result = severity._capture(check, {_SOURCE: sample}, window, foundation=True, **scope)
        else:
            result = severity._search(check, window, foundation=True, **scope)
        check()
        return result
    except SOURCE_ERRORS as error:
        if isinstance(error, OperationError):
            raise
        if capture:
            unavailable = severity._capture(authority, missing, window)
            if result and result["status"] in ("accepted", "unconfirmed"):
                unavailable["status"] = "unconfirmed"
            return unavailable
        return dict(operation="search", status="unavailable", since_minutes=10,
                    window_end=severity._utc(window[1]), limit=severity._SEARCH_LIMIT, hits=[])
    finally:
        authority()


def search(principal, role):
    return _operate(principal, role, False)


def capture(principal, role):
    return _operate(principal, role, True)
