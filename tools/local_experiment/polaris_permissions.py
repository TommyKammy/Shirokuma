#!/usr/bin/env python3
"""Allow Trino purge only inside the disposable local_experiment namespace.

Polaris 1.6.0 RbacOperationSemantics requires TABLE_DROP + TABLE_WRITE_DATA
for DROP_TABLE_WITH_PURGE. Existing catalog_admin supplies TABLE_DROP; this
helper adds only the missing namespace-scoped data privilege.

API contract: https://github.com/apache/polaris/blob/apache-polaris-1.6.0/spec/polaris-management-service.yml
"""

import argparse
import base64
import copy
import json
import subprocess
import sys
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener


CONTEXT = "colima-mac-studio-solo"
CATALOG = "shirokuma_l1"
NAMESPACE = "local_experiment"
ROLE = "local_experiment_purge"
PRINCIPAL_ROLE = "service_admin"
MARKER = {"shirokuma.dev/scope": "local-experiment"}
GRANT = {"type": "namespace", "namespace": [NAMESPACE], "privilege": "TABLE_WRITE_DATA"}
MANAGEMENT = "/api/management/v1"
ROLE_COLLECTION = f"{MANAGEMENT}/catalogs/{CATALOG}/catalog-roles"
ROLE_PATH = f"{ROLE_COLLECTION}/{ROLE}"
BINDING_PATH = f"{MANAGEMENT}/principal-roles/{PRINCIPAL_ROLE}/catalog-roles/{CATALOG}"
CATALOG_PATH = f"{MANAGEMENT}/catalogs/{CATALOG}"
PURGE_PROPERTY = "polaris.config.drop-with-purge.enabled"


class SafeError(RuntimeError):
    """An error whose message never includes a credential or response body."""


class ApiError(SafeError):
    def __init__(self, method, path, status):
        self.status = status
        super().__init__(f"Polaris {method} {path} returned HTTP {status}")


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, new_url):
        return None


class PolarisClient:
    def __init__(self, port):
        if not 1024 <= port <= 65535:
            raise SafeError("port must be between 1024 and 65535")
        self.base = f"http://127.0.0.1:{port}"
        self.opener = build_opener(ProxyHandler({}), NoRedirect())
        self.realm = ""
        self.token = ""

    def request(self, method, path, body=None, headers=None):
        if not path.startswith("/api/") or "://" in path:
            raise SafeError("invalid local Polaris API path")
        request_headers = {"Accept": "application/json", "Polaris-Realm": self.realm}
        if self.token:
            request_headers["Authorization"] = "Bearer " + self.token
        if headers:
            request_headers.update(headers)
        if isinstance(body, dict):
            data = json.dumps(body).encode()
            request_headers["Content-Type"] = "application/json"
        else:
            data = body
        request = Request(self.base + path, data=data, headers=request_headers, method=method)
        try:
            with self.opener.open(request, timeout=20) as response:
                payload = response.read()
                return json.loads(payload) if payload else {}
        except HTTPError as error:
            raise ApiError(method, path, error.code) from None
        except (URLError, OSError, ValueError):
            raise SafeError(f"Polaris {method} {path} failed; response details suppressed") from None

    def authenticate(self):
        result = subprocess.run(
            ["kubectl", "--context", CONTEXT, "-n", "shirokuma-dev", "get", "secret",
             "polaris-root-credentials", "-o", "json"],
            capture_output=True, text=True, check=False,
        )
        if result.returncode:
            raise SafeError("could not read local Polaris credentials")
        try:
            data = json.loads(result.stdout)["data"]
            values = {key: base64.b64decode(data[key], validate=True).decode()
                      for key in ("client_id", "client_secret", "realm")}
            if not all(values.values()):
                raise ValueError("empty credential")
        except (KeyError, ValueError, UnicodeError):
            raise SafeError("local Polaris credential Secret has an invalid shape") from None
        self.realm = values["realm"]
        basic = base64.b64encode(
            (values["client_id"] + ":" + values["client_secret"]).encode()
        ).decode()
        response = self.request(
            "POST", "/api/catalog/v1/oauth/tokens",
            urlencode({"grant_type": "client_credentials", "scope": "PRINCIPAL_ROLE:ALL"}).encode(),
            {"Authorization": "Basic " + basic, "Content-Type": "application/x-www-form-urlencoded"},
        )
        self.token = response.get("access_token", "")
        if not isinstance(self.token, str) or not self.token:
            raise SafeError("Polaris token response did not contain a token")


def checked_grants(response):
    grants = response.get("grants")
    if not isinstance(grants, list) or any(grant != GRANT for grant in grants):
        raise SafeError("experimental role has unexpected grants; refusing to broaden its assignment")
    return grants


def ensure_permissions(client):
    # An existing namespace is required; this helper does not create data scopes.
    client.request("GET", f"/api/catalog/v1/{CATALOG}/namespaces/{NAMESPACE}")
    actions = []
    try:
        role = client.request("GET", ROLE_PATH)
    except ApiError as error:
        if error.status != 404:
            raise
        client.request("POST", ROLE_COLLECTION, {"catalogRole": {"name": ROLE, "properties": MARKER}})
        actions.append("catalog-role-created")
        role = client.request("GET", ROLE_PATH)
    if role.get("name") != ROLE or role.get("properties", {}).get("shirokuma.dev/scope") != MARKER["shirokuma.dev/scope"]:
        raise SafeError("experimental role name is already used by an unmarked role")
    grants = checked_grants(client.request("GET", ROLE_PATH + "/grants"))
    if not grants:
        client.request("PUT", ROLE_PATH + "/grants", {"grant": GRANT})
        actions.append("namespace-write-data-granted")
    if not checked_grants(client.request("GET", ROLE_PATH + "/grants")):
        raise SafeError("namespace grant was not retained")
    bindings = client.request("GET", BINDING_PATH).get("roles")
    if not isinstance(bindings, list):
        raise SafeError("Polaris catalog role binding response has an invalid shape")
    if not any(role.get("name") == ROLE for role in bindings):
        client.request("PUT", BINDING_PATH, {"catalogRole": {"name": ROLE}})
        actions.append("catalog-role-assigned")
    bindings = client.request("GET", BINDING_PATH).get("roles", [])
    if not any(role.get("name") == ROLE for role in bindings):
        raise SafeError("experimental catalog role assignment was not retained")
    return {"result": "configured", "catalog": CATALOG, "namespace": NAMESPACE,
            "catalog_role": ROLE, "principal_role": PRINCIPAL_ROLE,
            "grant": GRANT, "actions": actions}


def enable_catalog_purge(client):
    """Enable the catalog capability after confirming the namespace-only grant."""
    if not checked_grants(client.request("GET", ROLE_PATH + "/grants")):
        raise SafeError("namespace grant must exist before enabling catalog purge")
    catalog = client.request("GET", CATALOG_PATH)
    version = catalog.get("entityVersion")
    properties = catalog.get("properties")
    storage = catalog.get("storageConfigInfo")
    if (catalog.get("name") != CATALOG or catalog.get("type") != "INTERNAL"
            or type(version) is not int or version < 0
            or not isinstance(properties, dict) or not isinstance(storage, dict)):
        raise SafeError("catalog identity, version, or configuration has an invalid shape")
    prior = properties.get(PURGE_PROPERTY)
    if prior not in (None, "true", "false"):
        raise SafeError("catalog purge property has an unexpected value")
    receipt = {
        "property": PURGE_PROPERTY,
        "scope": "catalog",
        "previous": {"present": PURGE_PROPERTY in properties, "value": prior,
                     "entity_version": version},
    }
    if prior == "true":
        receipt.update({"action": "already-enabled", "value": "true", "entity_version": version})
        return receipt
    updated_properties = copy.deepcopy(properties)
    updated_properties[PURGE_PROPERTY] = "true"
    # Polaris replaces the property map. Preserve every unrelated property and
    # the storage contract, and never retry a version conflict with stale data.
    client.request("PUT", CATALOG_PATH, {
        "currentEntityVersion": version,
        "properties": updated_properties,
        "storageConfigInfo": copy.deepcopy(storage),
    })
    result = client.request("GET", CATALOG_PATH)
    if (result.get("name") != CATALOG or result.get("type") != "INTERNAL"
            or result.get("properties") != updated_properties
            or result.get("storageConfigInfo") != storage
            or type(result.get("entityVersion")) is not int
            or result["entityVersion"] <= version):
        raise SafeError("catalog purge update could not be verified without configuration drift")
    receipt.update({"action": "enabled", "value": "true", "entity_version": result["entityVersion"]})
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=18181, help="Existing loopback Polaris port-forward")
    parser.add_argument("--apply", action="store_true", help="Apply the fixed namespace-only grant")
    parser.add_argument("--enable-catalog-purge", action="store_true",
                        help="Also enable purge capability on shirokuma_l1; requires --apply")
    args = parser.parse_args()
    if args.enable_catalog_purge and not args.apply:
        parser.error("--enable-catalog-purge requires --apply")
    if not args.apply:
        print(json.dumps({"result": "plan", "context": CONTEXT, "catalog": CATALOG,
                          "namespace": NAMESPACE, "catalog_role": ROLE,
                          "principal_role": PRINCIPAL_ROLE, "grant": GRANT}, sort_keys=True))
        return
    try:
        client = PolarisClient(args.port)
        client.authenticate()
        result = ensure_permissions(client)
        if args.enable_catalog_purge:
            result["catalog_purge"] = enable_catalog_purge(client)
        print(json.dumps(result, sort_keys=True))
    except SafeError as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
