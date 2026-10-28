"""Small fixtures follow catalog/server, catalog/bootstrap and Trino static YAML."""

from copy import deepcopy
import json
import unittest

from tools.local_experiment import database_service_poc as poc


def ref(name, secret="shared", key=None):
    return {"name": name, "valueFrom": {"secretKeyRef": {
        "name": secret, "key": key or name}}}


def env(container):
    return {e["name"]: e for e in container["env"]}


def fixture(kind, name):
    metadata = {"name": name, "namespace": "shirokuma-dev", "uid": "old-uid",
                "resourceVersion": "99", "managedFields": [{}], "generation": 8,
                "ownerReferences": [{"uid": "old-owner"}],
                "annotations": {"shirokuma.dev/polaris-credential-generation": "old",
                                "keep": "annotation"},
                "labels": {"app.kubernetes.io/name": "polaris", "controller-uid": "old",
                           "batch.kubernetes.io/job-name": "old"}}
    container = {
        "name": name, "image": "example/image@sha256:static", "imagePullPolicy": "IfNotPresent",
        "env": [{"name": "POLARIS_PERSISTENCE_TYPE", "value": "relational-jdbc"},
                {"name": "POLARIS_REALM_CONTEXT_REALMS", "value": "POLARIS"},
                {"name": "QUARKUS_DATASOURCE_JDBC_URL", "value": "jdbc:postgresql://polaris-postgresql:5432/polaris"},
                ref("QUARKUS_DATASOURCE_USERNAME", "polaris-postgresql-credentials", "username"),
                ref("QUARKUS_DATASOURCE_PASSWORD", "polaris-postgresql-credentials", "password"),
                ref("AWS_ACCESS_KEY_ID"), ref("AWS_SECRET_ACCESS_KEY"), ref("AWS_REGION"),
                ref("AWS_SESSION_TOKEN", "foreign"), ref("POLARIS_CLIENT_SECRET", "foreign")],
        "securityContext": {"runAsNonRoot": True, "readOnlyRootFilesystem": True},
        "resources": {"limits": {"memory": "2Gi"}},
        "readinessProbe": {"httpGet": {"path": "/q/health/ready", "port": 8182}},
        "volumeMounts": [{"name": "tmp", "mountPath": "/tmp"}]}
    pod = {"containers": [container], "securityContext": {"fsGroup": 10001},
           "automountServiceAccountToken": False, "volumes": [{"name": "tmp", "emptyDir": {}}]}
    return {"apiVersion": "batch/v1" if kind == "Job" else "apps/v1", "kind": kind,
            "metadata": deepcopy(metadata), "status": {"ready": True},
            "spec": {"selector": {"matchLabels": {"controller-uid": "old"}},
                     "template": {"metadata": deepcopy(metadata), "spec": pod}}}


def database_fixtures():
    deployment = fixture("Deployment", "polaris")
    deployment["spec"].update(replicas=0, strategy={"type": "Recreate"})
    job = fixture("Job", "polaris-admin")
    job["spec"].update(backoffLimit=6, ttlSecondsAfterFinished=90, manualSelector=True,
                       activeDeadlineSeconds=600)
    pod = job["spec"]["template"]["spec"]
    pod["restartPolicy"] = "OnFailure"
    pod["containers"][0].update(
        args=["bootstrap", "--credentials-file=/var/run/secrets/shirokuma/polaris/credentials.json"],
        volumeMounts=[{"name": "credentials", "mountPath": "/var/run/secrets/shirokuma/polaris", "readOnly": True}])
    pod["volumes"].append({"name": "credentials", "secret": {
        "secretName": "polaris-root-credentials", "defaultMode": 0o440,
        "items": [{"key": "credentials.json", "path": "credentials.json"}]}})
    policy = {"apiVersion": "networking.k8s.io/v1", "kind": "NetworkPolicy",
              "metadata": {"name": "polaris", "namespace": "shirokuma-dev"}, "spec": {
                  "podSelector": {"matchLabels": {"app.kubernetes.io/name": "polaris"}},
                  "policyTypes": ["Ingress", "Egress"],
                  "ingress": [{"from": [{"namespaceSelector": {"matchLabels": {
                      "kubernetes.io/metadata.name": "shirokuma-dev"}}}], "ports": [{"port": 8181}]}],
                  "egress": [{"to": [{"podSelector": {"matchLabels": {
                      "app.kubernetes.io/name": "polaris-postgresql"}}}], "ports": [{"port": 5432}]},
                             {"ports": [{"port": 8333}, {"port": 53, "protocol": "UDP"}]}]}}
    return deployment, job, policy


def iceberg_fixture():
    job = fixture("Job", "iceberg-table-bootstrap")
    job["spec"].update(ttlSecondsAfterFinished=10, backoffLimit=3)
    pod = job["spec"]["template"]["spec"]
    container = pod["containers"][0]
    container.update(command=["/usr/bin/java"], args=[
        "-Djava.util.logging.manager=org.jboss.logmanager.LogManager", "--class-path",
        "/deployments/lib/boot/*:/deployments/lib/main/*:/deployments/app/*",
        "/opt/shirokuma/IcebergBootstrap.java"])
    container["env"].append({"name": "S3_ENDPOINT", "value": "http://seaweedfs-s3:8333"})
    pod["volumes"].append({"name": "source", "configMap": {"name": "iceberg-table-bootstrap-source"}})
    container["volumeMounts"].append({"name": "source", "mountPath": "/opt/shirokuma", "readOnly": True})
    return job


class DatabaseServicePocTests(unittest.TestCase):
    def setUp(self):
        self.s3 = {db: {"accessKey": db + "-access", "secretKey": db + "-secret"}
                   for db in ("sales", "finance")}
        self.roots = {db: {"clientId": db + "-root", "clientSecret": db + "-root-secret"}
                      for db in self.s3}

    def test_names_and_invalid_databases(self):
        for db in self.s3:
            self.assertEqual(poc.service_name(db), "polaris-service-poc-" + db)
            self.assertEqual(poc.realm(db), db.upper() + "_POC")
            self.assertEqual(poc.catalog(db), "service_poc_" + db)
            self.assertEqual(poc.prefix(db), "s3://shirokuma-lakehouse/database-poc/" + db + "/service-poc")
            self.assertEqual(poc.endpoint(db), "http://polaris-service-poc-" + db + ".shirokuma-dev.svc.cluster.local:8181")
        for invalid in ("", "Sales", "other", "sales/../finance", None):
            for function in (poc.service_name, poc.realm, poc.catalog, poc.prefix, poc.endpoint):
                with self.assertRaises(ValueError):
                    function(invalid)
            with self.assertRaises(ValueError):
                poc.build_database(invalid, {}, {}, "region", *database_fixtures())
            with self.assertRaises(ValueError):
                poc.build_iceberg_job(invalid, iceberg_fixture())

    def test_database_isolation_preservation_and_root_file(self):
        templates = database_fixtures()
        before = deepcopy((templates, self.s3, self.roots))
        selectors = []
        for db in self.s3:
            with self.subTest(db=db):
                s3, root, deployment, service, policy, job = poc.build_database(
                    db, self.s3[db], self.roots[db], "ap-northeast-1", *templates)
                name = poc.service_name(db)
                self.assertEqual(s3["metadata"]["name"], name + "-s3")
                self.assertEqual(s3["stringData"], {"AWS_ACCESS_KEY_ID": db + "-access",
                    "AWS_SECRET_ACCESS_KEY": db + "-secret", "AWS_REGION": "ap-northeast-1"})
                self.assertEqual(root["metadata"]["name"], name + "-root")
                self.assertEqual(set(root["stringData"]), {"client_id", "client_secret", "realm", "credentials.json"})
                self.assertEqual(json.loads(root["stringData"]["credentials.json"]), {
                    poc.realm(db): {"client-id": db + "-root", "client-secret": db + "-root-secret"}})
                self.assertEqual(root["stringData"]["realm"], poc.realm(db))
                selector = deployment["spec"]["selector"]["matchLabels"]
                selectors.append(selector)
                self.assertEqual(selector, service["spec"]["selector"])
                self.assertEqual(selector, policy["spec"]["podSelector"]["matchLabels"])
                self.assertEqual(selector["shirokuma.dev/database"], db)
                self.assertEqual(service["spec"]["ports"][0]["port"], 8181)
                self.assertEqual(deployment["spec"]["replicas"], 1)
                self.assertEqual(policy["spec"]["ingress"], templates[2]["spec"]["ingress"])
                self.assertEqual(policy["spec"]["egress"], templates[2]["spec"]["egress"])
                for output, original in ((deployment, templates[0]), (job, templates[1])):
                    self.assertNotIn("status", output)
                    self.assertNotIn("uid", output["metadata"])
                    pod = output["spec"]["template"]
                    self.assertTrue(selector.items() <= pod["metadata"]["labels"].items() if output is deployment
                                    else pod["metadata"]["labels"]["app.kubernetes.io/component"] == "catalog-bootstrap")
                    self.assertEqual(pod["metadata"]["annotations"], {"keep": "annotation"})
                    c, old = pod["spec"]["containers"][0], original["spec"]["template"]["spec"]["containers"][0]
                    self.assertEqual(c["imagePullPolicy"], "Never")
                    for key in ("image", "securityContext", "resources", "readinessProbe", "volumeMounts"):
                        self.assertEqual(c[key], old[key])
                    for key, value in env(old).items():
                        if key.startswith("QUARKUS_") or key == "POLARIS_PERSISTENCE_TYPE":
                            self.assertEqual(env(c)[key], value)
                    self.assertEqual(env(c)["POLARIS_REALM_CONTEXT_REALMS"]["value"], poc.realm(db))
                    self.assertNotIn("AWS_SESSION_TOKEN", env(c))
                    self.assertNotIn("POLARIS_CLIENT_SECRET", env(c))
                for key in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_REGION"):
                    self.assertEqual(env(deployment["spec"]["template"]["spec"]["containers"][0])[key], ref(key, name + "-s3"))
                self.assertEqual(job["spec"]["backoffLimit"], 0)
                self.assertNotIn("ttlSecondsAfterFinished", job["spec"])
                self.assertNotIn("selector", job["spec"])
                self.assertNotIn("manualSelector", job["spec"])
                self.assertEqual(job["spec"]["template"]["spec"]["volumes"][1]["secret"]["secretName"], name + "-root")
        self.assertNotEqual(*selectors)
        self.assertEqual((templates, self.s3, self.roots), before)

    def test_iceberg_java_arguments_and_credentials(self):
        for db in self.s3:
            for flag in ("--class-path", "-classpath", "-cp"):
                template = iceberg_fixture()
                template["spec"]["template"]["spec"]["containers"][0]["args"][1] = flag
                before = deepcopy(template)
                job = poc.build_iceberg_job(db, template)
                pod = job["spec"]["template"]["spec"]
                c = pod["containers"][0]
                self.assertEqual(job["metadata"]["name"], "service-poc-bootstrap-" + db)
                self.assertEqual(c["command"], ["/usr/bin/java"])
                args = c["args"]
                for arg in ("-Dshirokuma.catalog=" + poc.catalog(db), "-Dshirokuma.namespace=bootstrap",
                            "-Dshirokuma.table=fixture_v1", "-Dshirokuma.base-location=" + poc.prefix(db)):
                    self.assertLess(args.index(arg), args.index(flag))
                self.assertEqual(args[args.index(flag) + 1:], before["spec"]["template"]["spec"]["containers"][0]["args"][2:])
                self.assertEqual(env(c)["POLARIS_URI"]["value"], poc.endpoint(db))
                for key, secret_key in (("POLARIS_CLIENT_ID", "client_id"), ("POLARIS_CLIENT_SECRET", "client_secret"), ("POLARIS_REALM", "realm")):
                    self.assertEqual(env(c)[key], ref(key, poc.service_name(db) + "-root", secret_key))
                for key in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_REGION"):
                    self.assertEqual(env(c)[key], ref(key, poc.service_name(db) + "-s3"))
                self.assertNotIn("AWS_SESSION_TOKEN", env(c))
                self.assertEqual(pod["volumes"], before["spec"]["template"]["spec"]["volumes"])
                self.assertEqual(c["volumeMounts"], before["spec"]["template"]["spec"]["containers"][0]["volumeMounts"])
                self.assertEqual(job["spec"]["backoffLimit"], 0)
                self.assertEqual(pod["restartPolicy"], "Never")
                self.assertNotIn("ttlSecondsAfterFinished", job["spec"])
                self.assertNotIn("controller-uid", job["spec"]["template"]["metadata"]["labels"])
                self.assertEqual(template, before)

    def test_trino_catalog_volume_auth_and_dedicated_pairs(self):
        deployment = fixture("Deployment", "trino")
        deployment["metadata"]["name"] = "trino-local"
        pod = deployment["spec"]["template"]["spec"]
        c = pod["containers"][0]
        c["env"].append(ref("TRINO_INTERNAL_SHARED_SECRET", "trino-local-auth", "shared-secret"))
        data = {"iceberg.properties": "connector.name=iceberg\ns3.endpoint=http://seaweedfs-s3:8333\ns3.path-style-access=true\niceberg.unique-table-location=true\ns3.region=${ENV:AWS_REGION}\n",
                "tpch.properties": "connector.name=tpch\n", "rules.json": '{"catalogs": []}',
                "config.properties": "http-server.authentication.type=PASSWORD\n",
                "password-authenticator.properties": "password-authenticator.name=file\n"}
        configmap = {"apiVersion": "v1", "kind": "ConfigMap", "metadata": {"name": "trino-local-config"}, "data": data}
        pod["volumes"].append({"name": "config", "configMap": {"name": "trino-local-config", "defaultMode": 0o440,
            "items": [{"key": k, "path": "catalog/" + k if k in ("iceberg.properties", "tpch.properties") else k} for k in data]}})
        before = deepcopy((deployment, configmap, self.s3, self.roots))
        config, secret, result = poc.build_trino(deployment, configmap, self.s3, self.roots, "eu-west-1")
        self.assertEqual(config["metadata"]["name"], "trino-service-poc-config")
        self.assertEqual(result["metadata"]["name"], "trino-local")
        self.assertNotIn("iceberg.properties", config["data"])
        for key in data.keys() - {"iceberg.properties"}:
            self.assertEqual(config["data"][key], data[key])
        output_c = result["spec"]["template"]["spec"]["containers"][0]
        for key in ("image", "imagePullPolicy", "resources", "readinessProbe", "securityContext", "volumeMounts"):
            self.assertEqual(output_c[key], c[key])
        output_env = env(output_c)
        self.assertEqual(output_env["TRINO_INTERNAL_SHARED_SECRET"], env(c)["TRINO_INTERNAL_SHARED_SECRET"])
        self.assertFalse(any(k.startswith(("AWS_", "POLARIS_")) for k in output_env))
        expected_keys = set()
        for db in self.s3:
            props = dict(line.split("=", 1) for line in config["data"][db + ".properties"].splitlines())
            self.assertEqual(props["iceberg.rest-catalog.uri"], poc.endpoint(db) + "/api/catalog")
            self.assertEqual(props["iceberg.rest-catalog.warehouse"], poc.catalog(db))
            self.assertEqual(props["iceberg.rest-catalog.http-headers"], "Polaris-Realm:" + poc.realm(db))
            self.assertEqual(props["s3.region"], "eu-west-1")
            self.assertEqual(props["s3.endpoint"], "http://seaweedfs-s3:8333")
            self.assertEqual(props["iceberg.unique-table-location"], "true")
            stem = db.upper() + "_"
            for key, value in (("AWS_ACCESS_KEY_ID", db + "-access"), ("AWS_SECRET_ACCESS_KEY", db + "-secret"),
                               ("POLARIS_CLIENT_ID", db + "-root"), ("POLARIS_CLIENT_SECRET", db + "-root-secret")):
                expected_keys.add(stem + key)
                self.assertEqual(secret["stringData"][stem + key], value)
                self.assertEqual(output_env[stem + key], ref(stem + key, "trino-service-poc-credentials"))
            self.assertEqual(props["iceberg.rest-catalog.oauth2.credential"], "${ENV:" + stem + "POLARIS_CLIENT_ID}:${ENV:" + stem + "POLARIS_CLIENT_SECRET}")
            self.assertEqual(props["s3.aws-access-key"], "${ENV:" + stem + "AWS_ACCESS_KEY_ID}")
            self.assertEqual(props["s3.aws-secret-key"], "${ENV:" + stem + "AWS_SECRET_ACCESS_KEY}")
        self.assertEqual(set(secret["stringData"]), expected_keys)
        volume = result["spec"]["template"]["spec"]["volumes"][1]["configMap"]
        self.assertEqual(volume["name"], "trino-service-poc-config")
        paths = {i["key"]: i["path"] for i in volume["items"]}
        self.assertNotIn("iceberg.properties", paths)
        for db in self.s3:
            self.assertEqual(paths[db + ".properties"], "catalog/" + db + ".properties")
        self.assertEqual(paths["tpch.properties"], "catalog/tpch.properties")
        self.assertEqual(paths["rules.json"], "rules.json")
        self.assertEqual((deployment, configmap, self.s3, self.roots), before)


if __name__ == "__main__":
    unittest.main()
