"""Loopback-only OpenMetadata API client; tokens stay in memory."""
from __future__ import annotations
import base64
import json
from pathlib import Path
import urllib.error
import urllib.request

URL = "http://127.0.0.1:18585/api/v1"


class APIError(RuntimeError):
    def __init__(self, method, path, status):
        self.status = status
        super().__init__(f"OpenMetadata {method} {path.split('?')[0]} failed with HTTP {status}")


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise RuntimeError("OpenMetadata endpoint must not redirect")


class Client:
    def __init__(self):
        self.token = None
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())

    def request(self, method, path, data=None):
        if not path.startswith("/") or path.startswith("//"):
            raise ValueError("API path must be relative")
        headers = {"Content-Type": "application/json"}
        if self.token:
            headers["Authorization"] = "Bearer " + self.token
        request = urllib.request.Request(URL + path, data=json.dumps(data).encode() if data is not None else None,
                                         headers=headers, method=method)
        try:
            with self.opener.open(request, timeout=30) as response:
                body = response.read(10 * 1024 * 1024 + 1)
                if len(body) > 10 * 1024 * 1024:
                    raise RuntimeError("OpenMetadata response exceeded limit")
                try:
                    return json.loads(body) if body else None
                except ValueError:
                    return body.decode()
        except urllib.error.HTTPError as error:
            raise APIError(method, path, error.code) from None

    def login(self, password):
        result = self.request("POST", "/users/login", {"email": "admin@open-metadata.org",
                 "password": base64.b64encode(password.encode()).decode()})
        self.token = result["accessToken"]

    def authenticate(self, state: Path):
        values = dict(line.split("=", 1) for line in (state / ".env").read_text().splitlines())
        password = values["OM_ADMIN_PASSWORD"]
        try:
            self.login(password)
        except APIError as error:
            if error.status not in {400, 401} or (state / "admin-initialized").exists():
                raise
            self.login("admin")
            self.request("PUT", "/users/changePassword", {"username": "admin", "oldPassword": "admin",
                         "newPassword": password, "confirmPassword": password, "requestType": "SELF"})
            self.login(password)
        (state / "admin-initialized").touch(mode=0o600)
