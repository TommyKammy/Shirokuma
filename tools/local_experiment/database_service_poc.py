"""Pure manifest builders for the bounded sales/finance database-service PoC.

TEST ONLY: Trino temporarily authenticates with each database's ROOT OAuth
credentials. These manifests are not an application-principal design. Callers
supply already parsed static templates and credentials; nothing is read, written,
executed or contacted here. Returned Secrets use Kubernetes stringData.
"""

from copy import deepcopy
import json


def service_name(db):
    if db not in ("sales", "finance"):
        raise ValueError("database must be sales or finance")
    return "polaris-service-poc-" + db


def realm(db):
    service_name(db)
    return db.upper() + "_POC"


def catalog(db):
    service_name(db)
    return "service_poc_" + db


def prefix(db):
    service_name(db)
    return "s3://shirokuma-lakehouse/database-poc/" + db + "/service-poc"


def endpoint(db):
    return "http://" + service_name(db) + ".shirokuma-dev.svc.cluster.local:8181"


def _labels(db, component="catalog-server"):
    service_name(db)
    return {"app.kubernetes.io/name": "polaris-service-poc",
            "app.kubernetes.io/component": component,
            "shirokuma.dev/database": db,
            "shirokuma.dev/scope": "database-service-poc"}


def _metadata(metadata, name=None, labels=None):
    clean = {k: deepcopy(v) for k, v in metadata.items()
             if k in ("name", "namespace", "labels", "annotations")}
    clean["annotations"] = {
        k: v for k, v in clean.get("annotations", {}).items()
        if "generation" not in k and k not in (
            "kubectl.kubernetes.io/last-applied-configuration",
            "deployment.kubernetes.io/revision")}
    clean["labels"] = {k: v for k, v in clean.get("labels", {}).items()
                       if k not in ("controller-uid", "job-name", "pod-template-hash")
                       and not k.startswith("batch.kubernetes.io/")}
    if name is not None:
        clean["name"] = name
    if labels is not None:
        clean["labels"].update(labels)
    return clean


def _clone(template, name=None, labels=None):
    result = deepcopy(template)
    result.pop("status", None)
    result["metadata"] = _metadata(result.get("metadata", {}), name, labels)
    if "template" in result.get("spec", {}):
        pod = result["spec"]["template"]
        pod["metadata"] = _metadata(pod.get("metadata", {}), labels=labels)
        pod["metadata"].pop("name", None)
        if result["kind"] == "Job":
            result["spec"].pop("selector", None)
            result["spec"].pop("manualSelector", None)
            result["spec"].pop("ttlSecondsAfterFinished", None)
            result["spec"]["backoffLimit"] = 0
            pod["spec"]["restartPolicy"] = "Never"
    return result


def _ref(name, secret, key):
    return {"name": name, "valueFrom": {"secretKeyRef": {"name": secret, "key": key}}}


def _env(container, additions, keep_persistence=False):
    container["env"] = [e for e in container.get("env", [])
                        if not e["name"].startswith(("AWS_", "POLARIS_", "SALES_", "FINANCE_"))
                        or (keep_persistence and e["name"] == "POLARIS_PERSISTENCE_TYPE")]
    container["env"].extend(additions)


def _s3_env(db):
    return [_ref(k, service_name(db) + "-s3", k)
            for k in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_REGION")]


def _secret(name, namespace, data):
    return {"apiVersion": "v1", "kind": "Secret", "type": "Opaque",
            "metadata": {"name": name, "namespace": namespace}, "stringData": data}


def _region(region):
    if not isinstance(region, str) or not region.strip() or any(c in region for c in "\r\n"):
        raise ValueError("region must be a nonempty single-line string")
    return region


def build_database(db, s3, root, region, polaris_template, bootstrap_template, network_template):
    """Return isolated S3/root Secrets, Polaris Deployment, Service, policy, Job."""
    name, labels = service_name(db), _labels(db)
    namespace = polaris_template["metadata"].get("namespace", "shirokuma-dev")
    s3_secret = _secret(name + "-s3", namespace, {
        "AWS_ACCESS_KEY_ID": s3["accessKey"], "AWS_SECRET_ACCESS_KEY": s3["secretKey"],
        "AWS_REGION": _region(region)})
    root_secret = _secret(name + "-root", namespace, {
        "client_id": root["clientId"], "client_secret": root["clientSecret"], "realm": realm(db),
        "credentials.json": json.dumps({realm(db): {
            "client-id": root["clientId"], "client-secret": root["clientSecret"]}})})
    deployment = _clone(polaris_template, name, labels)
    deployment["spec"]["replicas"] = 1
    deployment["spec"]["selector"] = {"matchLabels": labels.copy()}
    container = next(c for c in deployment["spec"]["template"]["spec"]["containers"]
                     if c["name"] == "polaris")
    _env(container, [{"name": "POLARIS_REALM_CONTEXT_REALMS", "value": realm(db)}]
         + _s3_env(db), keep_persistence=True)
    container["imagePullPolicy"] = "Never"
    service = {"apiVersion": "v1", "kind": "Service",
               "metadata": {"name": name, "namespace": namespace, "labels": labels.copy()},
               "spec": {"selector": labels.copy(), "ports": [
                   {"name": "http", "port": 8181, "targetPort": 8181, "protocol": "TCP"}]}}
    policy = _clone(network_template, name, labels)
    policy["spec"]["podSelector"] = {"matchLabels": labels.copy()}
    job = _clone(bootstrap_template, name + "-bootstrap", _labels(db, "catalog-bootstrap"))
    pod = job["spec"]["template"]["spec"]
    admin = next(c for c in pod["containers"] if c["name"] == "polaris-admin")
    _env(admin, [{"name": "POLARIS_REALM_CONTEXT_REALMS", "value": realm(db)}],
         keep_persistence=True)
    admin["imagePullPolicy"] = "Never"
    next(v for v in pod["volumes"] if v["name"] == "credentials")["secret"]["secretName"] = name + "-root"
    return [s3_secret, root_secret, deployment, service, policy, job]


def build_trino(deployment, configmap, credentials, roots, region):
    """Return PoC config/Secret and a cloned Trino Deployment (TEST ONLY roots)."""
    region = _region(region)
    result = _clone(deployment)
    config = _clone(configmap, "trino-service-poc-config")
    data = config["data"]
    base = data.pop("iceberg.properties", "connector.name=iceberg\n")
    secret_data, env = {}, []
    for db in ("sales", "finance"):
        stem = db.upper() + "_"
        pairs = {"AWS_ACCESS_KEY_ID": credentials[db]["accessKey"],
                 "AWS_SECRET_ACCESS_KEY": credentials[db]["secretKey"],
                 "POLARIS_CLIENT_ID": roots[db]["clientId"],
                 "POLARIS_CLIENT_SECRET": roots[db]["clientSecret"]}
        for key, value in pairs.items():
            secret_data[stem + key] = value
            env.append(_ref(stem + key, "trino-service-poc-credentials", stem + key))
        def variable(key):
            return "${ENV:" + stem + key + "}"
        properties = {
            "connector.name": "iceberg", "iceberg.catalog.type": "rest",
            "iceberg.rest-catalog.uri": endpoint(db) + "/api/catalog",
            "iceberg.rest-catalog.warehouse": catalog(db),
            "iceberg.rest-catalog.security": "OAUTH2",
            "iceberg.rest-catalog.oauth2.credential": variable("POLARIS_CLIENT_ID") + ":" + variable("POLARIS_CLIENT_SECRET"),
            "iceberg.rest-catalog.oauth2.scope": "PRINCIPAL_ROLE:ALL",
            "iceberg.rest-catalog.oauth2.server-uri": endpoint(db) + "/api/catalog/v1/oauth/tokens",
            "iceberg.rest-catalog.oauth2.token-exchange-enabled": "false",
            "iceberg.rest-catalog.http-headers": "Polaris-Realm:" + realm(db),
            "iceberg.rest-catalog.vended-credentials-enabled": "false",
            "fs.s3.enabled": "true", "s3.region": region,
            "s3.aws-access-key": variable("AWS_ACCESS_KEY_ID"),
            "s3.aws-secret-key": variable("AWS_SECRET_ACCESS_KEY")}
        retained = [line for line in base.splitlines()
                    if line.split("=", 1)[0].strip() not in properties]
        data[db + ".properties"] = "\n".join(retained + [k + "=" + v for k, v in properties.items()]) + "\n"
    pod = result["spec"]["template"]["spec"]
    _env(next(c for c in pod["containers"] if c["name"] == "trino"), env)
    volume = next(v["configMap"] for v in pod["volumes"]
                  if v.get("configMap", {}).get("name") == configmap["metadata"]["name"])
    volume["name"] = config["metadata"]["name"]
    if "items" in volume:
        volume["items"] = [i for i in volume["items"]
                           if i["key"] not in ("iceberg.properties", "sales.properties", "finance.properties")]
        volume["items"].extend({"key": db + ".properties", "path": "catalog/" + db + ".properties"}
                               for db in ("sales", "finance"))
    else:
        volume["items"] = [{"key": k, "path": "catalog/" + k if k in (
            "sales.properties", "finance.properties", "tpch.properties") else k} for k in data]
    namespace = result["metadata"].get("namespace", "shirokuma-dev")
    return [config, _secret("trino-service-poc-credentials", namespace, secret_data), result]


def build_iceberg_job(db, job_template):
    """Clone the static Java source-launcher Job, inserting JVM settings first."""
    service_name(db)
    job = _clone(job_template, "service-poc-bootstrap-" + db, _labels(db, "catalog-bootstrap"))
    container = next(c for c in job["spec"]["template"]["spec"]["containers"]
                     if c["name"] == "iceberg-table-bootstrap")
    settings = {"catalog": catalog(db), "namespace": "bootstrap", "table": "fixture_v1",
                "base-location": prefix(db)}
    args = [a for a in container["args"]
            if not any(a.startswith("-Dshirokuma." + k + "=") for k in settings)]
    index = next((i for i, a in enumerate(args) if a in ("--class-path", "-classpath", "-cp")), None)
    if index is None:
        raise ValueError("Iceberg Java template must contain a classpath argument")
    args[index:index] = ["-Dshirokuma." + k + "=" + v for k, v in settings.items()]
    container["args"] = args
    _env(container, [{"name": "POLARIS_URI", "value": endpoint(db)},
                     _ref("POLARIS_CLIENT_ID", service_name(db) + "-root", "client_id"),
                     _ref("POLARIS_CLIENT_SECRET", service_name(db) + "-root", "client_secret"),
                     _ref("POLARIS_REALM", service_name(db) + "-root", "realm")] + _s3_env(db))
    return job
