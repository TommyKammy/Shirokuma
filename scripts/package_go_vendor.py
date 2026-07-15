#!/usr/bin/env python3
"""Create and verify deterministic, source-bound Go vendor archives."""

from __future__ import annotations

import argparse
import base64
import binascii
import hashlib
import json
import lzma
import os
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path, PurePosixPath
from typing import Any, BinaryIO, Dict, Iterable, List, Optional, Sequence, Tuple


SCHEMA_VERSION = 2
GENERATOR_POLICY = "go-mod-vendor-authenticated-subset+canonical-tar-xz-v2"
ARCHIVE_FORMAT = "tar.xz"
CHUNK_SIZE = 1024 * 1024
SHA256_RE = re.compile(r"[0-9a-f]{64}")
COMMIT_RE = re.compile(r"[0-9a-f]{40}")
GO_IMAGE_RE = re.compile(r"[^\s@]+@sha256:[0-9a-f]{64}")
GO_VERSION_RE = re.compile(r"(?:go)?[0-9]+\.[0-9]+\.[0-9]+")
CANONICAL_MODES = {"0644": 0o644, "0755": 0o755}

TOP_LEVEL_KEYS = {"schema_version", "source", "generator", "modules", "archive"}
SOURCE_KEYS = {"commit", "go_mod_sha256", "go_sum_sha256"}
GENERATOR_KEYS = {"go_image", "go_version", "policy"}
MODULE_KEYS = {"path", "version", "sum", "go_mod_sum", "replacement"}
REPLACEMENT_KEYS = {"path", "version", "sum", "go_mod_sum"}
ARCHIVE_KEYS = {"format", "sha256", "files"}
FILE_KEYS = {"path", "size", "mode", "sha256"}
SOURCE_RECORD_MODULE_INPUT_KEYS = {
    "bundle",
    "bundle_sha256",
    "manifest",
    "manifest_sha256",
    "go_mod_sha256",
    "go_sum_sha256",
    "go_image",
    "go_version",
    "generator_policy",
    "module_count",
    "replacement_count",
    "file_count",
}


class VendorPackageError(RuntimeError):
    """An expected packaging or verification failure with a stable code."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail


def _fail(code: str, detail: str) -> None:
    raise VendorPackageError(code, detail)


def _expect(condition: bool, code: str, detail: str) -> None:
    if not condition:
        _fail(code, detail)


def _run_checked(
    command: Sequence[str],
    *,
    cwd: Path,
    env: Dict[str, str],
    code: str,
) -> str:
    try:
        result = subprocess.run(
            list(command),
            cwd=cwd,
            env=env,
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    except OSError as error:
        _fail(code, str(error))
    if result.returncode != 0:
        output = (result.stderr or result.stdout).strip().splitlines()
        _fail(code, output[-1] if output else f"exit {result.returncode}")
    return result.stdout


def _sha256_command_stdout(
    command: Sequence[str],
    *,
    cwd: Path,
    env: Dict[str, str],
    code: str,
) -> str:
    try:
        process = subprocess.Popen(
            list(command),
            cwd=cwd,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    except OSError as error:
        _fail(code, str(error))
    _expect(process.stdout is not None, code, "missing command stdout")
    digest = hashlib.sha256()
    for chunk in iter(lambda: process.stdout.read(CHUNK_SIZE), b""):
        digest.update(chunk)
    stderr = process.stderr.read() if process.stderr is not None else b""
    return_code = process.wait()
    if return_code != 0:
        output = stderr.decode("utf-8", errors="replace").strip().splitlines()
        _fail(code, output[-1] if output else f"exit {return_code}")
    return digest.hexdigest()


def _sha256_stream(stream: BinaryIO) -> Tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    while True:
        chunk = stream.read(CHUNK_SIZE)
        if not chunk:
            break
        digest.update(chunk)
        size += len(chunk)
    return digest.hexdigest(), size


def _sha256_file(path: Path) -> str:
    try:
        with path.open("rb") as stream:
            return _sha256_stream(stream)[0]
    except OSError as error:
        _fail("IO", f"cannot hash {path}: {error}")


def _load_json(path: Path, code: str = "MANIFEST_JSON") -> Any:
    try:
        with path.open("r", encoding="utf-8") as stream:
            return json.load(stream)
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        _fail(code, f"cannot read {path}: {error}")


def _load_json_stream(path: Path) -> List[Dict[str, Any]]:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        _fail("MODULE_GRAPH_JSON", f"cannot read {path}: {error}")
    decoder = json.JSONDecoder()
    records: List[Any] = []
    offset = 0
    try:
        while offset < len(text):
            while offset < len(text) and text[offset].isspace():
                offset += 1
            if offset == len(text):
                break
            value, offset = decoder.raw_decode(text, offset)
            if isinstance(value, list):
                records.extend(value)
            else:
                records.append(value)
    except json.JSONDecodeError as error:
        _fail("MODULE_GRAPH_JSON", f"cannot decode {path}: {error}")
    _expect(bool(records), "MODULE_GRAPH_JSON", "module graph is empty")
    for index, record in enumerate(records):
        _expect(
            isinstance(record, dict),
            "MODULE_GRAPH_RECORD",
            f"record {index} is not an object",
        )
    return records


def _validate_h1(value: Any, code: str, detail: str) -> str:
    _expect(isinstance(value, str) and value.startswith("h1:"), code, detail)
    try:
        decoded = base64.b64decode(value[3:], validate=True)
    except (binascii.Error, ValueError):
        _fail(code, detail)
    _expect(len(decoded) == hashlib.sha256().digest_size, code, detail)
    return value


def _module_identity(value: Any, field: str, detail: str) -> str:
    _expect(
        isinstance(value, str)
        and bool(value)
        and not any(character.isspace() for character in value)
        and "\x00" not in value,
        "MODULE_GRAPH_RECORD",
        f"{detail} has invalid {field}",
    )
    return value


def _module_path(value: Any, detail: str, replacement: bool = False) -> str:
    path = _module_identity(value, "path", detail)
    if replacement:
        _expect(
            not path.startswith((".", "/")) and "\\" not in path,
            "MODULE_REPLACEMENT_UNPINNED",
            f"{detail} uses a local replacement path",
        )
    return path


def _optional_h1(value: Any, detail: str) -> Optional[str]:
    if value in (None, ""):
        return None
    return _validate_h1(value, "MODULE_SUM", detail)


def _sanitize_module_graph(records: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    modules: List[Dict[str, Any]] = []
    identities = set()
    for index, raw in enumerate(records):
        detail = f"module record {index}"
        if raw.get("Main") is True and not raw.get("Version"):
            continue
        if raw.get("Error") not in (None, ""):
            _fail("MODULE_GRAPH_ERROR", f"{detail}: {raw['Error']}")
        path = _module_path(raw.get("Path"), detail)
        version = _module_identity(raw.get("Version"), "version", detail)
        identity = (path, version)
        _expect(identity not in identities, "MODULE_DUPLICATE", f"{path}@{version}")
        identities.add(identity)

        replacement_raw = raw.get("Replace")
        if replacement_raw is None:
            module_sum = _validate_h1(
                raw.get("Sum"), "MODULE_SUM", f"{path}@{version} has invalid Sum"
            )
            go_mod_sum = _validate_h1(
                raw.get("GoModSum"),
                "MODULE_SUM",
                f"{path}@{version} has invalid GoModSum",
            )
            replacement = None
        else:
            _expect(
                isinstance(replacement_raw, dict),
                "MODULE_GRAPH_RECORD",
                f"{path}@{version} replacement is not an object",
            )
            replacement_path = _module_path(
                replacement_raw.get("Path"), detail, replacement=True
            )
            replacement_version = replacement_raw.get("Version")
            _expect(
                isinstance(replacement_version, str) and bool(replacement_version),
                "MODULE_REPLACEMENT_UNPINNED",
                f"{path}@{version} replacement has no version",
            )
            replacement_version = _module_identity(
                replacement_version, "replacement version", detail
            )
            replacement = {
                "path": replacement_path,
                "version": replacement_version,
                "sum": _validate_h1(
                    replacement_raw.get("Sum"),
                    "MODULE_SUM",
                    f"{path}@{version} replacement has invalid Sum",
                ),
                "go_mod_sum": _validate_h1(
                    replacement_raw.get("GoModSum"),
                    "MODULE_SUM",
                    f"{path}@{version} replacement has invalid GoModSum",
                ),
            }
            module_sum = _optional_h1(
                raw.get("Sum"), f"{path}@{version} has invalid original Sum"
            )
            go_mod_sum = _optional_h1(
                raw.get("GoModSum"),
                f"{path}@{version} has invalid original GoModSum",
            )

        modules.append(
            {
                "path": path,
                "version": version,
                "sum": module_sum,
                "go_mod_sum": go_mod_sum,
                "replacement": replacement,
            }
        )
    _expect(bool(modules), "MODULE_GRAPH_RECORD", "no versioned modules found")
    modules.sort(key=lambda item: (item["path"], item["version"]))
    return modules


def _vendored_module_identities(modules_txt_path: Path) -> List[Tuple[str, str]]:
    try:
        lines = modules_txt_path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as error:
        _fail("VENDORED_MODULES", f"cannot read {modules_txt_path}: {error}")
    identities: List[Tuple[str, str]] = []
    seen = set()
    for line in lines:
        if not line.startswith("# ") or line.startswith("## "):
            continue
        left = line[2:].split(" => ", 1)[0].split()
        if len(left) == 1:
            continue  # replacement footer without the original version
        _expect(len(left) == 2, "VENDORED_MODULES", line)
        identity = (
            _module_path(left[0], line),
            _module_identity(left[1], "version", line),
        )
        _expect(identity not in seen, "VENDORED_MODULES", f"duplicate {identity}")
        seen.add(identity)
        identities.append(identity)
    _expect(bool(identities), "VENDORED_MODULES", "vendor/modules.txt is empty")
    _expect(
        identities == sorted(identities),
        "VENDORED_MODULES",
        "module headers are not ordered",
    )
    return identities


def _go_sum_entries(go_sum_path: Path) -> Dict[Tuple[str, str], str]:
    try:
        lines = go_sum_path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as error:
        _fail("GO_SUM", f"cannot read {go_sum_path}: {error}")
    entries: Dict[Tuple[str, str], str] = {}
    for index, line in enumerate(lines):
        fields = line.split()
        _expect(len(fields) == 3, "GO_SUM", f"line {index + 1}")
        path = _module_path(fields[0], f"go.sum line {index + 1}")
        version = _module_identity(fields[1], "version", f"go.sum line {index + 1}")
        value = _validate_h1(fields[2], "GO_SUM", f"go.sum line {index + 1}")
        key = (path, version)
        _expect(
            key not in entries or entries[key] == value,
            "GO_SUM",
            f"conflicting checksum for {path} {version}",
        )
        entries[key] = value
    return entries


def _select_authenticated_vendored_modules(
    modules: List[Dict[str, Any]],
    modules_txt_path: Path,
    go_sum_path: Path,
) -> List[Dict[str, Any]]:
    by_identity = {
        (module["path"], module["version"]): module for module in modules
    }
    sums = _go_sum_entries(go_sum_path)
    selected: List[Dict[str, Any]] = []
    for identity in _vendored_module_identities(modules_txt_path):
        module = by_identity.get(identity)
        _expect(module is not None, "VENDORED_MODULE_GRAPH", repr(identity))
        replacement = module["replacement"]
        effective = replacement if replacement is not None else module
        path = effective["path"]
        version = effective["version"]
        _expect(
            sums.get((path, version)) == effective["sum"]
            and sums.get((path, f"{version}/go.mod")) == effective["go_mod_sum"],
            "VENDORED_MODULE_SUM",
            f"{path}@{version} is not authenticated by the pinned go.sum",
        )
        selected.append(module)
    return selected


def _canonical_vendor_path(value: Any, code: str = "VENDOR_PATH") -> str:
    _expect(isinstance(value, str) and bool(value), code, "path is empty")
    _expect("\\" not in value and "\x00" not in value, code, str(value))
    path = PurePosixPath(value)
    parts = path.parts
    _expect(
        not path.is_absolute()
        and len(parts) >= 2
        and parts[0] == "vendor"
        and all(part not in ("", ".", "..") for part in parts)
        and path.as_posix() == value,
        code,
        value,
    )
    return value


def _canonical_mode(mode: int) -> Tuple[str, int]:
    canonical = 0o755 if mode & 0o111 else 0o644
    return f"{canonical:04o}", canonical


def _collect_vendor_files(vendor_dir: Path) -> Tuple[List[Dict[str, Any]], Dict[str, Path]]:
    try:
        root_stat = vendor_dir.lstat()
    except OSError as error:
        _fail("VENDOR_ROOT", f"cannot stat {vendor_dir}: {error}")
    _expect(stat.S_ISDIR(root_stat.st_mode), "VENDOR_ROOT", f"not a directory: {vendor_dir}")
    _expect(not stat.S_ISLNK(root_stat.st_mode), "VENDOR_ROOT", f"symlink: {vendor_dir}")

    records: List[Dict[str, Any]] = []
    paths: Dict[str, Path] = {}
    try:
        walker = os.walk(vendor_dir, topdown=True, followlinks=False)
        for current, directory_names, file_names in walker:
            directory_names.sort()
            file_names.sort()
            current_path = Path(current)
            for name in directory_names:
                directory = current_path / name
                entry_stat = directory.lstat()
                _expect(
                    stat.S_ISDIR(entry_stat.st_mode)
                    and not stat.S_ISLNK(entry_stat.st_mode),
                    "VENDOR_ENTRY_TYPE",
                    f"non-directory or symlink: {directory}",
                )
            for name in file_names:
                path = current_path / name
                entry_stat = path.lstat()
                _expect(
                    stat.S_ISREG(entry_stat.st_mode)
                    and not stat.S_ISLNK(entry_stat.st_mode),
                    "VENDOR_ENTRY_TYPE",
                    f"non-regular file or symlink: {path}",
                )
                relative = path.relative_to(vendor_dir).as_posix()
                archive_path = _canonical_vendor_path(f"vendor/{relative}")
                _expect(archive_path not in paths, "VENDOR_DUPLICATE", archive_path)
                mode_text, _ = _canonical_mode(entry_stat.st_mode)
                digest = _sha256_file(path)
                records.append(
                    {
                        "path": archive_path,
                        "size": entry_stat.st_size,
                        "mode": mode_text,
                        "sha256": digest,
                    }
                )
                paths[archive_path] = path
    except OSError as error:
        _fail("IO", f"cannot scan {vendor_dir}: {error}")
    records.sort(key=lambda item: item["path"])
    _expect(bool(records), "VENDOR_ROOT", "vendor tree has no regular files")
    return records, paths


def _tar_info(record: Dict[str, Any]) -> tarfile.TarInfo:
    info = tarfile.TarInfo(record["path"])
    info.size = record["size"]
    info.mode = CANONICAL_MODES[record["mode"]]
    info.mtime = 0
    info.uid = 0
    info.gid = 0
    info.uname = ""
    info.gname = ""
    info.type = tarfile.REGTYPE
    info.pax_headers = {}
    return info


def _write_canonical_archive(
    archive_path: Path,
    records: Sequence[Dict[str, Any]],
    paths: Dict[str, Path],
) -> None:
    try:
        archive_path.parent.mkdir(parents=True, exist_ok=True)
        with lzma.LZMAFile(
            archive_path,
            mode="wb",
            format=lzma.FORMAT_XZ,
            check=lzma.CHECK_CRC64,
            preset=9,
        ) as compressed:
            with tarfile.open(
                fileobj=compressed, mode="w", format=tarfile.PAX_FORMAT
            ) as archive:
                for record in records:
                    with paths[record["path"]].open("rb") as source:
                        archive.addfile(_tar_info(record), source)
    except (OSError, lzma.LZMAError, tarfile.TarError) as error:
        _fail("ARCHIVE_CREATE", f"cannot create {archive_path}: {error}")


def _validate_module_manifest(modules: Any) -> List[Dict[str, Any]]:
    _expect(isinstance(modules, list) and bool(modules), "MANIFEST_MODULES", "empty")
    previous: Optional[Tuple[str, str]] = None
    seen = set()
    for index, module in enumerate(modules):
        detail = f"manifest module {index}"
        _expect(
            isinstance(module, dict) and set(module) == MODULE_KEYS,
            "MANIFEST_MODULE_SCHEMA",
            detail,
        )
        path = _module_path(module.get("path"), detail)
        version = _module_identity(module.get("version"), "version", detail)
        replacement = module.get("replacement")
        if replacement is None:
            _validate_h1(module.get("sum"), "MODULE_SUM", detail)
            _validate_h1(module.get("go_mod_sum"), "MODULE_SUM", detail)
        else:
            _expect(
                isinstance(replacement, dict) and set(replacement) == REPLACEMENT_KEYS,
                "MANIFEST_REPLACEMENT_SCHEMA",
                detail,
            )
            _module_path(replacement.get("path"), detail, replacement=True)
            _module_identity(replacement.get("version"), "replacement version", detail)
            _validate_h1(replacement.get("sum"), "MODULE_SUM", detail)
            _validate_h1(replacement.get("go_mod_sum"), "MODULE_SUM", detail)
            _optional_h1(module.get("sum"), detail)
            _optional_h1(module.get("go_mod_sum"), detail)
        identity = (path, version)
        _expect(identity not in seen, "MODULE_DUPLICATE", f"{path}@{version}")
        _expect(previous is None or previous < identity, "MANIFEST_MODULE_ORDER", detail)
        seen.add(identity)
        previous = identity
    return modules


def _validate_file_manifest(files: Any) -> List[Dict[str, Any]]:
    _expect(isinstance(files, list) and bool(files), "MANIFEST_FILES", "empty")
    previous: Optional[str] = None
    seen = set()
    for index, record in enumerate(files):
        detail = f"manifest file {index}"
        _expect(
            isinstance(record, dict) and set(record) == FILE_KEYS,
            "MANIFEST_FILE_SCHEMA",
            detail,
        )
        path = _canonical_vendor_path(record.get("path"), "MANIFEST_FILE_PATH")
        _expect(path not in seen, "MANIFEST_FILE_DUPLICATE", path)
        _expect(previous is None or previous < path, "MANIFEST_FILE_ORDER", path)
        _expect(
            isinstance(record.get("size"), int)
            and not isinstance(record.get("size"), bool)
            and record["size"] >= 0,
            "MANIFEST_FILE_SIZE",
            path,
        )
        _expect(record.get("mode") in CANONICAL_MODES, "MANIFEST_FILE_MODE", path)
        _expect(
            isinstance(record.get("sha256"), str)
            and SHA256_RE.fullmatch(record["sha256"]) is not None,
            "MANIFEST_FILE_HASH",
            path,
        )
        seen.add(path)
        previous = path
    return files


def _validate_manifest(data: Any) -> Dict[str, Any]:
    _expect(
        isinstance(data, dict) and set(data) == TOP_LEVEL_KEYS,
        "MANIFEST_SCHEMA",
        "unexpected or missing top-level field",
    )
    _expect(
        data.get("schema_version") == SCHEMA_VERSION,
        "MANIFEST_VERSION",
        f"expected {SCHEMA_VERSION}",
    )
    source = data.get("source")
    _expect(
        isinstance(source, dict) and set(source) == SOURCE_KEYS,
        "MANIFEST_SOURCE_SCHEMA",
        "unexpected or missing source field",
    )
    _expect(COMMIT_RE.fullmatch(source.get("commit", "")) is not None, "SOURCE_COMMIT", "invalid")
    for field in ("go_mod_sha256", "go_sum_sha256"):
        _expect(
            SHA256_RE.fullmatch(source.get(field, "")) is not None,
            "SOURCE_HASH",
            field,
        )
    generator = data.get("generator")
    _expect(
        isinstance(generator, dict) and set(generator) == GENERATOR_KEYS,
        "MANIFEST_GENERATOR_SCHEMA",
        "unexpected or missing generator field",
    )
    _expect(
        GO_IMAGE_RE.fullmatch(generator.get("go_image", "")) is not None,
        "GO_IMAGE_PIN",
        "Go image must use an immutable sha256 digest",
    )
    _expect(
        GO_VERSION_RE.fullmatch(generator.get("go_version", "")) is not None,
        "GO_VERSION_PIN",
        str(generator.get("go_version")),
    )
    _expect(
        generator.get("policy") == GENERATOR_POLICY,
        "GENERATOR_POLICY",
        str(generator.get("policy")),
    )
    _validate_module_manifest(data.get("modules"))
    archive = data.get("archive")
    _expect(
        isinstance(archive, dict) and set(archive) == ARCHIVE_KEYS,
        "MANIFEST_ARCHIVE_SCHEMA",
        "unexpected or missing archive field",
    )
    _expect(archive.get("format") == ARCHIVE_FORMAT, "ARCHIVE_FORMAT", "expected tar.xz")
    _expect(
        SHA256_RE.fullmatch(archive.get("sha256", "")) is not None,
        "ARCHIVE_HASH",
        "invalid manifest archive hash",
    )
    _validate_file_manifest(archive.get("files"))
    return data


def _render_manifest(manifest: Dict[str, Any]) -> str:
    """Render large repeated records one-per-line without losing reviewability."""

    compact = lambda value: json.dumps(  # noqa: E731 - local canonical encoder
        value, sort_keys=True, separators=(",", ":")
    )
    lines = ["{", '  "archive": {', '    "files": [']
    files = manifest["archive"]["files"]
    for index, record in enumerate(files):
        suffix = "," if index + 1 < len(files) else ""
        lines.append(f"      {compact(record)}{suffix}")
    lines.extend(
        [
            "    ],",
            f'    "format": {compact(manifest["archive"]["format"])},',
            f'    "sha256": {compact(manifest["archive"]["sha256"])}',
            "  },",
            f'  "generator": {compact(manifest["generator"])},',
            '  "modules": [',
        ]
    )
    modules = manifest["modules"]
    for index, record in enumerate(modules):
        suffix = "," if index + 1 < len(modules) else ""
        lines.append(f"    {compact(record)}{suffix}")
    lines.extend(
        [
            "  ],",
            f'  "schema_version": {manifest["schema_version"]},',
            f'  "source": {compact(manifest["source"])}',
            "}",
        ]
    )
    return "\n".join(lines) + "\n"


def create_package(
    *,
    vendor_dir: Path,
    module_graph_path: Path,
    source_commit: str,
    go_mod_path: Path,
    go_sum_path: Path,
    go_image: str,
    go_version: str,
    archive_path: Path,
    manifest_path: Path,
) -> Dict[str, Any]:
    _expect(COMMIT_RE.fullmatch(source_commit) is not None, "SOURCE_COMMIT", source_commit)
    _expect(GO_IMAGE_RE.fullmatch(go_image) is not None, "GO_IMAGE_PIN", go_image)
    _expect(GO_VERSION_RE.fullmatch(go_version) is not None, "GO_VERSION_PIN", go_version)
    vendor_root = vendor_dir.resolve()
    for output in (archive_path, manifest_path):
        try:
            output.resolve().relative_to(vendor_root)
        except ValueError:
            pass
        else:
            _fail("ARCHIVE_OUTPUT", f"output is inside vendor tree: {output}")
    _expect(archive_path.resolve() != manifest_path.resolve(), "ARCHIVE_OUTPUT", "archive and manifest paths match")

    files, paths = _collect_vendor_files(vendor_dir)
    modules = _select_authenticated_vendored_modules(
        _sanitize_module_graph(_load_json_stream(module_graph_path)),
        vendor_dir / "modules.txt",
        go_sum_path,
    )
    _write_canonical_archive(archive_path, files, paths)
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "source": {
            "commit": source_commit,
            "go_mod_sha256": _sha256_file(go_mod_path),
            "go_sum_sha256": _sha256_file(go_sum_path),
        },
        "generator": {
            "go_image": go_image,
            "go_version": go_version,
            "policy": GENERATOR_POLICY,
        },
        "modules": modules,
        "archive": {
            "format": ARCHIVE_FORMAT,
            "sha256": _sha256_file(archive_path),
            "files": files,
        },
    }
    _validate_manifest(manifest)
    try:
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        manifest_path.write_text(_render_manifest(manifest), encoding="utf-8")
    except OSError as error:
        _fail("IO", f"cannot write {manifest_path}: {error}")
    verify_package(
        archive_path=archive_path,
        manifest_path=manifest_path,
        expected_source_commit=source_commit,
        go_mod_path=go_mod_path,
        go_sum_path=go_sum_path,
        expected_go_image=go_image,
        expected_go_version=go_version,
    )
    return manifest


def _validate_xz_header(path: Path) -> None:
    try:
        with path.open("rb") as stream:
            header = stream.read(6)
    except OSError as error:
        _fail("IO", f"cannot read {path}: {error}")
    _expect(
        header == b"\xfd7zXZ\x00",
        "XZ_HEADER",
        "invalid XZ stream header",
    )


def _extract_and_validate_archive(
    archive_path: Path,
    files: Sequence[Dict[str, Any]],
    destination: Path,
) -> Dict[str, Path]:
    expected = {record["path"]: record for record in files}
    extracted: Dict[str, Path] = {}
    try:
        with tarfile.open(archive_path, mode="r:xz") as archive:
            for member in archive:
                _expect(member.isreg(), "ARCHIVE_MEMBER_TYPE", member.name)
                name = _canonical_vendor_path(member.name, "ARCHIVE_MEMBER_PATH")
                _expect(name not in extracted, "ARCHIVE_DUPLICATE", name)
                _expect(name in expected, "ARCHIVE_FILE_SET", f"unexpected: {name}")
                record = expected[name]
                _expect(
                    member.uid == 0
                    and member.gid == 0
                    and member.uname == ""
                    and member.gname == ""
                    and member.mtime == 0
                    and member.mode == CANONICAL_MODES[record["mode"]],
                    "ARCHIVE_METADATA",
                    name,
                )
                _expect(member.size == record["size"], "ARCHIVE_FILE_SIZE", name)
                source = archive.extractfile(member)
                _expect(source is not None, "ARCHIVE_MEMBER_TYPE", name)
                target = destination.joinpath(*PurePosixPath(name).parts)
                target.parent.mkdir(parents=True, exist_ok=True)
                digest = hashlib.sha256()
                size = 0
                with target.open("wb") as output:
                    while True:
                        chunk = source.read(CHUNK_SIZE)
                        if not chunk:
                            break
                        output.write(chunk)
                        digest.update(chunk)
                        size += len(chunk)
                _expect(size == record["size"], "ARCHIVE_FILE_SIZE", name)
                _expect(digest.hexdigest() == record["sha256"], "ARCHIVE_FILE_HASH", name)
                target.chmod(CANONICAL_MODES[record["mode"]])
                extracted[name] = target
    except VendorPackageError:
        raise
    except (OSError, lzma.LZMAError, tarfile.TarError) as error:
        _fail("ARCHIVE_READ", f"cannot read {archive_path}: {error}")
    _expect(set(extracted) == set(expected), "ARCHIVE_FILE_SET", "missing archive member")
    return extracted


def verify_package(
    *,
    archive_path: Path,
    manifest_path: Path,
    expected_source_commit: Optional[str] = None,
    go_mod_path: Optional[Path] = None,
    go_sum_path: Optional[Path] = None,
    expected_go_image: Optional[str] = None,
    expected_go_version: Optional[str] = None,
    source_record_path: Optional[Path] = None,
    verify_archive_contents: bool = True,
) -> Dict[str, Any]:
    manifest = _validate_manifest(_load_json(manifest_path))
    source = manifest["source"]
    generator = manifest["generator"]
    if expected_source_commit is not None:
        _expect(
            source["commit"] == expected_source_commit,
            "EXPECTED_SOURCE_COMMIT",
            source["commit"],
        )
    if expected_go_image is not None:
        _expect(
            generator["go_image"] == expected_go_image,
            "EXPECTED_GO_IMAGE",
            generator["go_image"],
        )
    if expected_go_version is not None:
        _expect(
            generator["go_version"] == expected_go_version,
            "EXPECTED_GO_VERSION",
            generator["go_version"],
        )
    if go_mod_path is not None:
        _expect(
            _sha256_file(go_mod_path) == source["go_mod_sha256"],
            "EXPECTED_GO_MOD",
            str(go_mod_path),
        )
    if go_sum_path is not None:
        _expect(
            _sha256_file(go_sum_path) == source["go_sum_sha256"],
            "EXPECTED_GO_SUM",
            str(go_sum_path),
        )
    _expect(
        _sha256_file(archive_path) == manifest["archive"]["sha256"],
        "ARCHIVE_HASH",
        str(archive_path),
    )
    _validate_xz_header(archive_path)

    if source_record_path is not None:
        source_record = _load_json(source_record_path, "SOURCE_RECORD_JSON")
        _expect(isinstance(source_record, dict), "SOURCE_RECORD_SCHEMA", "not an object")
        module_inputs = source_record.get("module_inputs")
        _expect(
            isinstance(module_inputs, dict)
            and set(module_inputs) == SOURCE_RECORD_MODULE_INPUT_KEYS,
            "SOURCE_RECORD_SCHEMA",
            "module_inputs has unexpected or missing fields",
        )
        expected_module_inputs = {
            "bundle": archive_path.name,
            "bundle_sha256": manifest["archive"]["sha256"],
            "manifest": manifest_path.name,
            "manifest_sha256": _sha256_file(manifest_path),
            "go_mod_sha256": source["go_mod_sha256"],
            "go_sum_sha256": source["go_sum_sha256"],
            "go_image": generator["go_image"],
            "go_version": generator["go_version"],
            "generator_policy": generator["policy"],
            "module_count": len(manifest["modules"]),
            "replacement_count": sum(
                module["replacement"] is not None
                for module in manifest["modules"]
            ),
            "file_count": len(manifest["archive"]["files"]),
        }
        _expect(
            source_record.get("commit") == source["commit"]
            and module_inputs == expected_module_inputs,
            "SOURCE_RECORD_BINDING",
            "source commit or module_inputs differs from the manifest",
        )

    if verify_archive_contents:
        with tempfile.TemporaryDirectory(prefix="go-vendor-verify-") as directory:
            temporary = Path(directory)
            _extract_and_validate_archive(
                archive_path, manifest["archive"]["files"], temporary
            )
    return manifest


def _compare_reproduced_inputs(
    manifest: Dict[str, Any],
    reproduced_vendor_dir: Path,
    go_sum_path: Path,
    reproduced_module_graph_path: Optional[Path] = None,
) -> None:
    module_candidates = manifest["modules"]
    if reproduced_module_graph_path is not None:
        module_candidates = _sanitize_module_graph(
            _load_json_stream(reproduced_module_graph_path)
        )
    reproduced_modules = _select_authenticated_vendored_modules(
        module_candidates,
        reproduced_vendor_dir / "modules.txt",
        go_sum_path,
    )
    _expect(
        reproduced_modules == manifest["modules"],
        "REPRODUCED_MODULE_GRAPH",
        "clean-cache module graph differs from the retained manifest",
    )
    reproduced_files, _ = _collect_vendor_files(reproduced_vendor_dir)
    _expect(
        reproduced_files == manifest["archive"]["files"],
        "REPRODUCED_VENDOR_CONTENT",
        "clean-cache vendor tree differs from the retained manifest",
    )


def verify_reproduced_package(
    *,
    source_root: Path,
    source_record_path: Path,
    archive_path: Path,
    manifest_path: Path,
    reproduced_vendor_dir: Path,
    reproduced_module_graph_path: Path,
) -> Dict[str, Any]:
    """Bind a retained package to independently regenerated Go inputs."""

    source_record = _load_json(source_record_path, "SOURCE_RECORD_JSON")
    _expect(isinstance(source_record, dict), "SOURCE_RECORD_SCHEMA", "not an object")
    manifest = verify_package(
        archive_path=archive_path,
        manifest_path=manifest_path,
        expected_source_commit=source_record.get("commit"),
        go_mod_path=source_root / "go.mod",
        go_sum_path=source_root / "go.sum",
        source_record_path=source_record_path,
    )
    _compare_reproduced_inputs(
        manifest,
        reproduced_vendor_dir,
        source_root / "go.sum",
        reproduced_module_graph_path,
    )
    return manifest


def reproduce_package(
    *,
    source_root: Path,
    source_record_path: Path,
    archive_path: Path,
    manifest_path: Path,
    go_command: str = "go",
    git_command: str = "git",
) -> Dict[str, Any]:
    """Regenerate vendor inputs from an authenticated, fresh module cache."""

    source_root = source_root.resolve()
    source_record = _load_json(source_record_path, "SOURCE_RECORD_JSON")
    _expect(isinstance(source_record, dict), "SOURCE_RECORD_SCHEMA", "not an object")
    commit = source_record.get("commit")
    tree = source_record.get("tree")
    _expect(COMMIT_RE.fullmatch(commit or "") is not None, "REPRODUCTION_SOURCE", "commit")
    _expect(COMMIT_RE.fullmatch(tree or "") is not None, "REPRODUCTION_SOURCE", "tree")
    _expect(
        not (source_root / "vendor").exists(),
        "REPRODUCTION_SOURCE",
        "source checkout must not contain an ambient vendor directory",
    )

    manifest = verify_package(
        archive_path=archive_path,
        manifest_path=manifest_path,
        expected_source_commit=commit,
        go_mod_path=source_root / "go.mod",
        go_sum_path=source_root / "go.sum",
        source_record_path=source_record_path,
    )
    expected_go_version = manifest["generator"]["go_version"]
    go_binary = shutil.which(go_command)
    git_binary = shutil.which(git_command)
    _expect(go_binary is not None, "REPRODUCTION_GO", f"not found: {go_command}")
    _expect(git_binary is not None, "REPRODUCTION_GIT", f"not found: {git_command}")

    base_env = {
        "PATH": os.environ.get("PATH", ""),
        "LC_ALL": "C",
        "GIT_CONFIG_COUNT": "1",
        "GIT_CONFIG_KEY_0": "safe.directory",
        "GIT_CONFIG_VALUE_0": str(source_root),
    }
    head = _run_checked(
        [git_binary, "rev-parse", "HEAD"],
        cwd=source_root,
        env=base_env,
        code="REPRODUCTION_SOURCE",
    ).strip()
    actual_tree = _run_checked(
        [git_binary, "rev-parse", "HEAD^{tree}"],
        cwd=source_root,
        env=base_env,
        code="REPRODUCTION_SOURCE",
    ).strip()
    status = _run_checked(
        [git_binary, "status", "--porcelain=v1", "--untracked-files=all"],
        cwd=source_root,
        env=base_env,
        code="REPRODUCTION_SOURCE",
    ).strip()
    actual_archive_sha256 = _sha256_command_stdout(
        [git_binary, "archive", "--format=tar", "HEAD"],
        cwd=source_root,
        env=base_env,
        code="REPRODUCTION_SOURCE",
    )
    _expect(
        head == commit
        and actual_tree == tree
        and actual_archive_sha256 == source_record.get("git_archive_sha256")
        and not status,
        "REPRODUCTION_SOURCE",
        "source checkout is not the exact clean recorded commit, tree, and archive",
    )

    before_go_mod = _sha256_file(source_root / "go.mod")
    before_go_sum = _sha256_file(source_root / "go.sum")
    with tempfile.TemporaryDirectory(prefix="go-vendor-reproduce-") as directory:
        temporary = Path(directory)
        paths = {
            name: temporary / name
            for name in ("gocache", "gomodcache", "gopath", "home", "tmp")
        }
        for path in paths.values():
            path.mkdir()
        reproduction_env = {
            "PATH": os.environ.get("PATH", ""),
            "HOME": str(paths["home"]),
            "TMPDIR": str(paths["tmp"]),
            "LC_ALL": "C",
            "CGO_ENABLED": "0",
            "GO111MODULE": "on",
            "GOARCH": "arm64",
            "GOAUTH": "off",
            "GOCACHE": str(paths["gocache"]),
            "GOENV": "off",
            "GOFLAGS": "-mod=readonly",
            "GOINSECURE": "",
            "GOMODCACHE": str(paths["gomodcache"]),
            "GONOPROXY": "",
            "GONOSUMDB": "",
            "GOOS": "linux",
            "GOPATH": str(paths["gopath"]),
            "GOPRIVATE": "",
            "GOPROXY": "https://proxy.golang.org",
            "GOSUMDB": "sum.golang.org",
            "GOTOOLCHAIN": "local",
            "GOVCS": "*:off",
            "GOWORK": "off",
        }
        actual_go_version = _run_checked(
            [go_binary, "env", "GOVERSION"],
            cwd=source_root,
            env=reproduction_env,
            code="REPRODUCTION_GO_VERSION",
        ).strip()
        _expect(
            actual_go_version == f"go{expected_go_version}",
            "REPRODUCTION_GO_VERSION",
            actual_go_version,
        )
        authenticated_vendor_dir = temporary / "authenticated-vendor"
        _run_checked(
            [go_binary, "mod", "vendor", "-o", str(authenticated_vendor_dir)],
            cwd=source_root,
            env=reproduction_env,
            code="REPRODUCTION_DOWNLOAD",
        )
        _run_checked(
            [go_binary, "mod", "verify"],
            cwd=source_root,
            env=reproduction_env,
            code="REPRODUCTION_CACHE_VERIFY",
        )
        _compare_reproduced_inputs(
            manifest,
            authenticated_vendor_dir,
            source_root / "go.sum",
        )

        offline_env = dict(reproduction_env)
        offline_env.update({"GOPROXY": "off", "GOSUMDB": "off"})
        reproduced_vendor_dir = temporary / "vendor"
        _run_checked(
            [go_binary, "mod", "vendor", "-o", str(reproduced_vendor_dir)],
            cwd=source_root,
            env=offline_env,
            code="REPRODUCED_VENDOR_CONTENT",
        )
        _run_checked(
            [go_binary, "mod", "verify"],
            cwd=source_root,
            env=offline_env,
            code="REPRODUCTION_CACHE_VERIFY",
        )
        _compare_reproduced_inputs(
            manifest,
            reproduced_vendor_dir,
            source_root / "go.sum",
        )

    after_status = _run_checked(
        [git_binary, "status", "--porcelain=v1", "--untracked-files=all"],
        cwd=source_root,
        env=base_env,
        code="REPRODUCTION_SOURCE",
    ).strip()
    after_go_mod = _sha256_file(source_root / "go.mod")
    after_go_sum = _sha256_file(source_root / "go.sum")
    mutations = []
    if after_status:
        mutations.append("git status is not clean")
    if after_go_mod != before_go_mod:
        mutations.append("go.mod changed")
    if after_go_sum != before_go_sum:
        mutations.append("go.sum changed")
    _expect(
        not mutations,
        "REPRODUCTION_SOURCE_MUTATION",
        ", ".join(mutations),
    )
    return manifest


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    create = subparsers.add_parser("create", help="create archive and manifest")
    create.add_argument("--source-root", type=Path, required=True)
    create.add_argument("--module-list", type=Path, required=True)
    create.add_argument("--source-commit", required=True)
    create.add_argument("--go-image", required=True)
    create.add_argument("--go-version", required=True)
    create.add_argument("--bundle", type=Path, required=True)
    create.add_argument("--manifest", type=Path, required=True)

    verify = subparsers.add_parser("verify", help="verify archive and manifest")
    verify.add_argument("--source-record", type=Path, required=True)
    verify.add_argument("--bundle", type=Path, required=True)
    verify.add_argument("--manifest", type=Path, required=True)
    verify.add_argument("--expected-source-commit")
    verify.add_argument("--go-mod", type=Path)
    verify.add_argument("--go-sum", type=Path)
    verify.add_argument("--expected-go-image")
    verify.add_argument("--expected-go-version")

    reproduce = subparsers.add_parser(
        "reproduce",
        help="regenerate vendor inputs with a fresh authenticated module cache",
    )
    reproduce.add_argument("--source-root", type=Path, required=True)
    reproduce.add_argument("--source-record", type=Path, required=True)
    reproduce.add_argument("--bundle", type=Path, required=True)
    reproduce.add_argument("--manifest", type=Path, required=True)
    reproduce.add_argument("--go-command", default="go")
    reproduce.add_argument("--git-command", default="git")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        if arguments.command == "create":
            create_package(
                vendor_dir=arguments.source_root / "vendor",
                module_graph_path=arguments.module_list,
                source_commit=arguments.source_commit,
                go_mod_path=arguments.source_root / "go.mod",
                go_sum_path=arguments.source_root / "go.sum",
                go_image=arguments.go_image,
                go_version=arguments.go_version,
                archive_path=arguments.bundle,
                manifest_path=arguments.manifest,
            )
        elif arguments.command == "verify":
            verify_package(
                archive_path=arguments.bundle,
                manifest_path=arguments.manifest,
                expected_source_commit=arguments.expected_source_commit,
                go_mod_path=arguments.go_mod,
                go_sum_path=arguments.go_sum,
                expected_go_image=arguments.expected_go_image,
                expected_go_version=arguments.expected_go_version,
                source_record_path=arguments.source_record,
            )
        else:
            reproduce_package(
                source_root=arguments.source_root,
                source_record_path=arguments.source_record,
                archive_path=arguments.bundle,
                manifest_path=arguments.manifest,
                go_command=arguments.go_command,
                git_command=arguments.git_command,
            )
    except VendorPackageError as error:
        print(f"ERROR[{error.code}]: {error.detail}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
