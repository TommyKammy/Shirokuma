"""Versioned, explicit Parquet file lists (no hashing or glob expansion)."""
import hashlib
import json
from pathlib import Path
import re


def _version(version):
    if type(version) is not int or version not in (1, 2):
        raise ValueError("unsupported dataset format")


def _integer(value, positive=False):
    if type(value) is not int or value < (1 if positive else 0):
        raise ValueError("invalid file count/size")
    return value


def _digest(value):
    if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise ValueError("invalid SHA256")
    return value


def _filename(value, version):
    if (not isinstance(value, str) or not value or value in (".", "..") or
            (version == 2 and not value.endswith(".parquet")) or
            Path(value).name != value or "/" in value or "\\" in value or
            any(c in "*?[]" or ord(c) < 32 or ord(c) == 127 for c in value)):
        raise ValueError("flat filename required; format 2 requires .parquet")
    return value


def table_files(root, table, version=1, check=lambda: None):
    _version(version)
    if version == 1:
        if "files" in table:
            raise ValueError("legacy table requires only file")
        entries = [table]
    else:
        if "file" in table or not isinstance(table.get("files"), list) or not table["files"]:
            raise ValueError("nonempty explicit files required")
        entries = table["files"]
    names = [_filename(entry["file"], version) for entry in entries]
    root = Path(root).resolve()
    result, seen = [], set()
    for entry, name in zip(entries, names):
        check()
        if name in seen:
            raise ValueError("duplicate Parquet file")
        seen.add(name)
        path = (root / name).resolve()
        if path.parent != root or not path.is_file():
            raise ValueError("source outside root or missing")
        stat = path.stat()
        fallback = {"file_bytes": stat.st_size, "physical_bytes": getattr(stat, "st_blocks", (stat.st_size + 511) // 512) * 512}
        item = dict(file=name, path=path, rows=_integer(entry["rows"]))
        for key in fallback:
            item[key] = _integer(entry[key] if key in entry else fallback[key] if version == 1 else None,
                                 positive=key == "file_bytes")
        digest = entry.get("sha256")
        item["sha256"] = _digest(digest) if version == 2 or digest is not None else None
        result.append(item)
    if version == 2:
        for key in ("rows", "file_bytes", "physical_bytes"):
            if _integer(table[key], positive=key == "file_bytes") != sum(f[key] for f in result):
                raise ValueError("file aggregate mismatch")
    return result


def read_sql(root, table, version=1):
    _version(version)
    from .engine import quote
    if version == 1:
        return "SELECT * FROM read_parquet(" + quote(Path(root) / table["file"]) + ")"
    files = table_files(root, table, version)
    return "SELECT * FROM read_parquet([" + ", ".join(quote(f["path"]) for f in files) + "], union_by_name=false)"


def file_identity(files, version=1):
    _version(version)
    if not isinstance(files, list) or not files or (version == 1 and len(files) != 1):
        raise ValueError("invalid file list")
    canonical, seen = [], set()
    for f in files:
        name = _filename(f["file"], version)
        if name in seen:
            raise ValueError("duplicate Parquet file")
        seen.add(name)
        if "physical_bytes" in f:
            _integer(f["physical_bytes"])
        canonical.append(dict(file=name, rows=_integer(f["rows"]),
                              file_bytes=_integer(f["file_bytes"], True), sha256=_digest(f["sha256"])))
    if version == 1:
        return canonical[0]["sha256"]
    return hashlib.sha256(json.dumps(canonical, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf8")).hexdigest()
