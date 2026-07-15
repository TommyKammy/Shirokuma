#!/usr/bin/env python3
"""Small dependency-free SigV4 S3 client for the local-lite object store."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import hmac
import json
import os
import stat
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


DEFAULT_ENDPOINT = "http://127.0.0.1:18333"
DEFAULT_REGION = "us-east-1"
DEFAULT_IDENTITY_NAME = "shirokuma-local-lite-operator"


class S3ClientError(RuntimeError):
    """Safe-to-print client error that never includes credential material."""


@dataclass(frozen=True)
class S3Credentials:
    access_key: str
    secret_key: str
    session_token: str | None = None


@dataclass(frozen=True)
class S3Object:
    key: str
    size: int
    etag: str


def _validated_credentials(credentials: S3Credentials) -> S3Credentials:
    if (
        not credentials.access_key
        or len(credentials.access_key) > 128
        or not all(
            character.isascii()
            and (character.isalnum() or character in "._-")
            for character in credentials.access_key
        )
    ):
        raise S3ClientError("S3 access key has an invalid format")
    if not credentials.secret_key or any(
        character in "\r\n" for character in credentials.secret_key
    ):
        raise S3ClientError("S3 secret key has an invalid format")
    if credentials.session_token and any(
        character in "\r\n" for character in credentials.session_token
    ):
        raise S3ClientError("S3 session token has an invalid format")
    return credentials


def _read_secret_file(path: Path) -> dict[str, object]:
    no_follow = getattr(os, "O_NOFOLLOW", None)
    if no_follow is None:
        raise S3ClientError("platform cannot safely open S3 credentials file")
    descriptor = -1
    try:
        descriptor = os.open(path, os.O_RDONLY | no_follow)
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode):
            raise S3ClientError(
                "S3 credentials file must be a regular non-symlink file"
            )
        if stat.S_IMODE(info.st_mode) & 0o077:
            raise S3ClientError("S3 credentials file must be owner-only")
        stream = os.fdopen(descriptor, "r", encoding="utf-8")
        descriptor = -1
        with stream:
            value = json.load(stream)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise S3ClientError("cannot parse S3 credentials file") from error
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    if not isinstance(value, dict):
        raise S3ClientError("S3 credentials file root must be an object")
    return value


def _credentials_from_seaweedfs_config(
    config: dict[str, object], identity_name: str | None
) -> S3Credentials:
    identities = config.get("identities")
    if not isinstance(identities, list):
        raise S3ClientError("S3 credentials file must contain an identities array")

    candidates: list[tuple[str, str]] = []
    for identity in identities:
        if not isinstance(identity, dict):
            continue
        if identity_name is not None and identity.get("name") != identity_name:
            continue
        credentials = identity.get("credentials")
        if not isinstance(credentials, list):
            continue
        for credential in credentials:
            if not isinstance(credential, dict):
                continue
            access_key = credential.get("accessKey")
            secret_key = credential.get("secretKey")
            if isinstance(access_key, str) and isinstance(secret_key, str):
                candidates.append((access_key, secret_key))

    if len(candidates) != 1:
        suffix = " for S3_IDENTITY_NAME" if identity_name is not None else ""
        raise S3ClientError(
            f"S3 credentials file must select exactly one credential{suffix}"
        )
    access_key, secret_key = candidates[0]
    if not access_key or not secret_key:
        raise S3ClientError("selected S3 credential must not be empty")
    return _validated_credentials(
        S3Credentials(access_key=access_key, secret_key=secret_key)
    )


def load_credentials(environ: dict[str, str] | None = None) -> S3Credentials:
    env = os.environ if environ is None else environ
    access_key = env.get("AWS_ACCESS_KEY_ID")
    secret_key = env.get("AWS_SECRET_ACCESS_KEY")
    session_token = env.get("AWS_SESSION_TOKEN")
    credentials_file = env.get("S3_CREDENTIALS_FILE")

    has_environment_credentials = access_key is not None or secret_key is not None
    if has_environment_credentials and credentials_file:
        raise S3ClientError(
            "choose either AWS credential environment variables or S3_CREDENTIALS_FILE"
        )
    if has_environment_credentials:
        if not access_key or not secret_key:
            raise S3ClientError(
                "AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY must both be set"
            )
        return _validated_credentials(
            S3Credentials(access_key, secret_key, session_token)
        )
    if credentials_file:
        identity_name = env.get("S3_IDENTITY_NAME", DEFAULT_IDENTITY_NAME)
        return _credentials_from_seaweedfs_config(
            _read_secret_file(Path(credentials_file)), identity_name
        )
    raise S3ClientError(
        "set AWS_ACCESS_KEY_ID/AWS_SECRET_ACCESS_KEY or S3_CREDENTIALS_FILE"
    )


def _encode(value: str) -> str:
    return urllib.parse.quote(value, safe="-_.~")


class SigV4S3Client:
    def __init__(
        self,
        endpoint: str,
        credentials: S3Credentials,
        region: str = DEFAULT_REGION,
        timeout_seconds: float = 30,
    ) -> None:
        parsed = urllib.parse.urlsplit(endpoint)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
        ):
            raise S3ClientError("S3_ENDPOINT must be an HTTP(S) origin without credentials")
        self._origin = urllib.parse.urlunsplit(
            (parsed.scheme, parsed.netloc, "", "", "")
        )
        self._base_path = (
            parsed.path[:-1] if parsed.path.endswith("/") else parsed.path
        )
        self._host = parsed.netloc
        self._credentials = credentials
        self._region = region
        self._timeout_seconds = timeout_seconds
        self._opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    @staticmethod
    def _sign(key: bytes, message: str) -> bytes:
        return hmac.new(key, message.encode("utf-8"), hashlib.sha256).digest()

    def _authorization(
        self,
        method: str,
        canonical_uri: str,
        canonical_query: str,
        payload_hash: str,
        now: dt.datetime,
        headers: dict[str, str],
    ) -> str:
        amz_date = now.strftime("%Y%m%dT%H%M%SZ")
        date_stamp = now.strftime("%Y%m%d")
        canonical_header_names = sorted(name.lower() for name in headers)
        canonical_headers = "".join(
            f"{name}:{headers[name].strip()}\n" for name in canonical_header_names
        )
        signed_headers = ";".join(canonical_header_names)
        canonical_request = "\n".join(
            (
                method,
                canonical_uri,
                canonical_query,
                canonical_headers,
                signed_headers,
                payload_hash,
            )
        )
        scope = f"{date_stamp}/{self._region}/s3/aws4_request"
        string_to_sign = "\n".join(
            (
                "AWS4-HMAC-SHA256",
                amz_date,
                scope,
                hashlib.sha256(canonical_request.encode("utf-8")).hexdigest(),
            )
        )
        date_key = self._sign(
            ("AWS4" + self._credentials.secret_key).encode("utf-8"), date_stamp
        )
        region_key = self._sign(date_key, self._region)
        service_key = self._sign(region_key, "s3")
        signing_key = self._sign(service_key, "aws4_request")
        signature = hmac.new(
            signing_key, string_to_sign.encode("utf-8"), hashlib.sha256
        ).hexdigest()
        return (
            "AWS4-HMAC-SHA256 "
            f"Credential={self._credentials.access_key}/{scope},"
            f"SignedHeaders={signed_headers},Signature={signature}"
        )

    def _canonical_uri(self, path_segments: Iterable[str]) -> str:
        encoded_path = "/".join(_encode(segment) for segment in path_segments)
        canonical_uri = f"{self._base_path}/{encoded_path}"
        if not canonical_uri.startswith("/"):
            canonical_uri = "/" + canonical_uri
        return canonical_uri

    def request(
        self,
        method: str,
        path_segments: Iterable[str] = (),
        *,
        query: Iterable[tuple[str, str]] = (),
        body: bytes = b"",
        expected_statuses: frozenset[int] = frozenset({200}),
    ) -> bytes:
        canonical_uri = self._canonical_uri(path_segments)
        encoded_query = sorted((_encode(key), _encode(value)) for key, value in query)
        canonical_query = "&".join(f"{key}={value}" for key, value in encoded_query)
        payload_hash = hashlib.sha256(body).hexdigest()
        now = dt.datetime.now(dt.timezone.utc)
        headers = {
            "host": self._host,
            "x-amz-content-sha256": payload_hash,
            "x-amz-date": now.strftime("%Y%m%dT%H%M%SZ"),
        }
        if self._credentials.session_token:
            headers["x-amz-security-token"] = self._credentials.session_token
        headers["authorization"] = self._authorization(
            method, canonical_uri, canonical_query, payload_hash, now, headers
        )
        url = self._origin + canonical_uri
        if canonical_query:
            url += "?" + canonical_query
        request = urllib.request.Request(
            url,
            data=body if method in {"POST", "PUT"} else None,
            headers=headers,
            method=method,
        )
        try:
            with self._opener.open(request, timeout=self._timeout_seconds) as response:
                response_body = response.read()
                if response.status not in expected_statuses:
                    raise S3ClientError(
                        f"S3 request failed: {method} {canonical_uri} status={response.status}"
                    )
                return response_body
        except urllib.error.HTTPError as error:
            error.read()
            raise S3ClientError(
                f"S3 request failed: {method} {canonical_uri} status={error.code}"
            ) from error
        except urllib.error.URLError as error:
            raise S3ClientError(
                f"S3 request failed: {method} {canonical_uri} connection error"
            ) from error

    def create_bucket(self, bucket: str) -> None:
        self.request("PUT", (bucket,), expected_statuses=frozenset({200, 201}))

    def delete_bucket(self, bucket: str) -> None:
        self.request("DELETE", (bucket,), expected_statuses=frozenset({200, 204}))

    def put_object(self, bucket: str, key: str, body: bytes) -> None:
        self.request(
            "PUT",
            (bucket, *key.split("/")),
            body=body,
            expected_statuses=frozenset({200, 201}),
        )

    def get_object(self, bucket: str, key: str) -> bytes:
        return self.request("GET", (bucket, *key.split("/")))

    def delete_object(self, bucket: str, key: str) -> None:
        self.request(
            "DELETE",
            (bucket, *key.split("/")),
            expected_statuses=frozenset({200, 204}),
        )

    def list_objects(self, bucket: str, prefix: str = "") -> list[S3Object]:
        objects: list[S3Object] = []
        continuation_token: str | None = None
        while True:
            query = [("list-type", "2")]
            if prefix:
                query.append(("prefix", prefix))
            if continuation_token:
                query.append(("continuation-token", continuation_token))
            body = self.request("GET", (bucket,), query=query)
            try:
                root = ET.fromstring(body)
            except ET.ParseError as error:
                raise S3ClientError("S3 ListObjects response is malformed") from error

            def child_text(element: ET.Element, name: str) -> str:
                child = element.find(f"{{*}}{name}")
                return "" if child is None or child.text is None else child.text

            for content in root.findall("{*}Contents"):
                key = child_text(content, "Key")
                size_text = child_text(content, "Size")
                if not key or not size_text.isdigit():
                    raise S3ClientError("S3 ListObjects response has an invalid object")
                objects.append(
                    S3Object(
                        key=key,
                        size=int(size_text),
                        etag=child_text(content, "ETag").strip('"'),
                    )
                )
            if child_text(root, "IsTruncated").lower() != "true":
                break
            continuation_token = child_text(root, "NextContinuationToken")
            if not continuation_token:
                raise S3ClientError("truncated S3 listing has no continuation token")
        return objects


def run_smoke(client: SigV4S3Client, bucket: str) -> None:
    # A flat key proves CRUD without leaving an empty directory marker that would
    # make the fail-closed DeleteBucket operation report BucketNotEmpty.
    key = "round-trip-payload.bin"
    payload = os.urandom(64)
    bucket_created = False
    object_created = False
    try:
        client.create_bucket(bucket)
        bucket_created = True
        client.put_object(bucket, key, payload)
        object_created = True
        if client.get_object(bucket, key) != payload:
            raise S3ClientError("S3 round-trip payload mismatch")
        listed = {item.key for item in client.list_objects(bucket)}
        if key not in listed:
            raise S3ClientError("S3 round-trip object is absent from authenticated listing")
        client.delete_object(bucket, key)
        object_created = False
        for _ in range(20):
            if key not in {item.key for item in client.list_objects(bucket)}:
                break
            client.delete_object(bucket, key)
            time.sleep(0.25)
        else:
            raise S3ClientError("S3 round-trip object deletion did not converge")
        _delete_bucket_eventually(client, bucket)
        bucket_created = False
    finally:
        if object_created:
            try:
                client.delete_object(bucket, key)
            except S3ClientError:
                pass
        if bucket_created:
            try:
                _delete_bucket_eventually(client, bucket)
            except S3ClientError:
                pass


def _delete_bucket_eventually(
    client: SigV4S3Client, bucket: str, attempts: int = 20
) -> None:
    last_error: S3ClientError | None = None
    for _ in range(attempts):
        try:
            client.delete_bucket(bucket)
            return
        except S3ClientError as error:
            last_error = error
            time.sleep(0.25)
    assert last_error is not None
    raise last_error


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Authenticated local-lite S3 helper")
    subparsers = parser.add_subparsers(dest="command", required=True)
    smoke = subparsers.add_parser("smoke")
    smoke.add_argument("--bucket", required=True)
    args = parser.parse_args(argv)
    try:
        client = SigV4S3Client(
            os.environ.get("S3_ENDPOINT", DEFAULT_ENDPOINT),
            load_credentials(),
            os.environ.get("S3_REGION", DEFAULT_REGION),
        )
        if args.command == "smoke":
            run_smoke(client, args.bucket)
            print(f"object-storage-smoke: passed bucket={args.bucket}")
            return 0
    except S3ClientError as error:
        print(f"object-storage-s3: {error}", file=sys.stderr)
        return 1
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
