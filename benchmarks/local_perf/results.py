"""Typed JSONL stream with mandatory terminal footer and disk-backed full comparison."""
import datetime as dt
from decimal import Decimal
import hashlib
import itertools
import json
import math
from pathlib import Path
import re
import sqlite3
import tempfile


def dumps(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def column(name, kind):
    kind = re.sub(r"\s+", " ", str(kind).lower()).replace(", ", ",")
    aliases = {"int": "integer", "int32": "integer", "int64": "bigint", "int128": "hugeint",
               "string": "varchar", "double precision": "double", "float": "real",
               "timestamp": "timestamp(6)", "timestamp with time zone": "timestamp(6) with time zone",
               "timestamptz": "timestamp(6) with time zone"}
    kind = aliases.get(kind, kind)
    # Trino bounded VARCHAR is the same lossless string value domain here.
    # Adapters retain the native bounded contract separately in attempt evidence.
    if re.fullmatch(r"varchar\((0|[1-9][0-9]*)\)", kind):
        kind = "varchar"
    if kind not in ("tinyint", "smallint", "integer", "bigint", "hugeint", "ubigint",
                    "varchar", "boolean", "date", "double", "real") and not (
            re.fullmatch(r"decimal\(\d+,\d+\)", kind) or
            re.fullmatch(r"timestamp\([0-6]\)( with time zone)?", kind)):
        raise ValueError("unsupported result type: " + kind)
    if kind.startswith("decimal"):
        precision, scale = map(int, re.findall(r"\d+", kind))
        if not 1 <= precision <= 38 or not 0 <= scale <= precision:
            raise ValueError("invalid decimal precision/scale")
    return {"name": name, "type": kind}


def cell(value, kind):
    if value is None:
        return None
    if "int" in kind:
        if isinstance(value, bool) or not re.fullmatch(r"-?\d+", str(value)):
            raise ValueError("invalid integer")
        bits = {"tinyint": 8, "smallint": 16, "integer": 32, "bigint": 64, "hugeint": 128, "ubigint": 64}[kind]
        low, high = (0, 2**bits - 1) if kind == "ubigint" else (-2**(bits-1), 2**(bits-1)-1)
        if not low <= int(value) <= high:
            raise ValueError("integer outside declared range")
        return ["i", str(int(value))]
    if kind.startswith("decimal"):
        if isinstance(value, float):
            raise ValueError("decimal received as binary float")
        dec = Decimal(str(value))
        if not dec.is_finite():
            raise ValueError("nonfinite decimal")
        precision, scale = map(int, re.findall(r"\d+", kind))
        s = format(dec, "f")
        s = s.rstrip("0").rstrip(".") if "." in s else s
        s = "0" if dec == 0 else s
        frac = len(s.split(".")[1]) if "." in s else 0
        integer_digits = len(s.lstrip("-").split(".")[0].lstrip("0"))
        if frac > scale or integer_digits > precision - scale:
            raise ValueError("decimal outside declared precision/scale")
        return ["d", s]
    if kind in ("double", "real"):
        if isinstance(value, bool):
            raise ValueError("boolean in floating result")
        f = float(value)
        if not math.isfinite(f):
            raise ValueError("nonfinite floating result")
        return ["f", repr(f)]
    if kind == "boolean":
        if not isinstance(value, bool):
            raise ValueError("invalid boolean")
        return ["b", value]
    if kind == "date":
        if isinstance(value, dt.datetime):
            raise ValueError("timestamp in date column")
        d = value if isinstance(value, dt.date) else dt.date.fromisoformat(value)
        return ["date", d.isoformat()]
    if kind.startswith("timestamp"):
        s = str(value).replace("Z", "+00:00")
        frac = re.search(r"\.(\d+)", s)
        precision = int(re.search(r"\((\d)\)", kind)[1])
        if frac and any(c != "0" for c in frac[1][precision:]):
            raise ValueError("timestamp exceeds frozen precision")
        d = value if isinstance(value, dt.datetime) else dt.datetime.fromisoformat(s)
        # Protocol interpretation: timezone-free SQL timestamps are UTC, explicitly.
        d = d.replace(tzinfo=dt.timezone.utc) if d.tzinfo is None else d.astimezone(dt.timezone.utc)
        return ["ts", d.isoformat(timespec="microseconds")]
    if not isinstance(value, str):
        raise ValueError("invalid varchar")
    return ["s", value]


class Writer:
    def __init__(self, path, columns, metadata):
        self.columns = [column(c["name"], c["type"]) for c in columns]
        self.f = Path(path).open("x", encoding="utf8")
        self.count, self.digest = 0, hashlib.sha256()
        self.f.write(dumps({"format": 1, "columns": self.columns, "metadata": metadata,
                            "timestamp_policy": "UTC; naive interpreted UTC; schema precision exact"}) + "\n")

    def row(self, values):
        if len(values) != len(self.columns):
            raise ValueError("row width mismatch")
        line = dumps([cell(v, c["type"]) for v, c in zip(values, self.columns)]) + "\n"
        self.digest.update(line.encode())
        self.f.write(line)
        self.count += 1

    def finish(self):
        self.f.write(dumps({"terminal": "SUCCESS", "rows": self.count,
                            "sha256": self.digest.hexdigest()}) + "\n")
        self.f.close()

    def close(self):
        self.f.close()  # No success footer on failure.


class Reader:
    def __init__(self, path):
        self.f = Path(path).open(encoding="utf8")
        try:
            self.header = json.loads(self.f.readline())
            if (set(self.header) != {"format", "columns", "metadata", "timestamp_policy"}
                    or type(self.header["format"]) is not int or self.header["format"] != 1
                    or not isinstance(self.header["metadata"], dict)
                    or self.header["timestamp_policy"] != "UTC; naive interpreted UTC; schema precision exact"):
                raise ValueError("invalid header")
            for c in self.header["columns"]:
                if c != column(c["name"], c["type"]):
                    raise ValueError("noncanonical column")
        except BaseException:
            self.f.close()
            raise

    def __iter__(self):
        digest, count = hashlib.sha256(), 0
        try:
            for line in self.f:
                if not line.endswith("\n"):
                    raise ValueError("truncated line")
                value = json.loads(line)
                if isinstance(value, dict):
                    if type(value.get("rows")) is not int or value != {"terminal": "SUCCESS", "rows": count, "sha256": digest.hexdigest()}:
                        raise ValueError("invalid terminal footer")
                    if self.f.read(1):
                        raise ValueError("data after footer")
                    return
                if not isinstance(value, list) or len(value) != len(self.header["columns"]):
                    raise ValueError("malformed result row")
                for v, c in zip(value, self.header["columns"]):
                    if v is not None:
                        if not isinstance(v, list) or len(v) != 2:
                            raise ValueError("malformed typed cell")
                        raw = v[1]
                        if cell(raw, c["type"]) != v:
                            raise ValueError("noncanonical typed cell")
                digest.update(line.encode())
                count += 1
                yield value
            raise ValueError("EOF without explicit SUCCESS")
        finally:
            self.f.close()


def equal(a, b, abs_tol, rel_tol):
    if len(a) != len(b):
        return False
    return all(x == y or (x is not None and y is not None and x[0] == y[0] == "f"
                         and math.isclose(float(x[1]), float(y[1]), abs_tol=abs_tol, rel_tol=rel_tol))
               for x, y in zip(a, b))


def exact_key(row):
    return dumps([None if v is not None and v[0] == "f" else v for v in row])


def compare(left, right, *, ordered=False, abs_tol=0.0, rel_tol=0.0, scratch=None, check=lambda: None):
    """Full values, duplicates and footer verified; SQLite cache limited to 8 MiB.

    Tolerant unordered rows use disk-backed augmenting-path matching, avoiding
    the false negatives of greedy float matching. Cost may be high for large
    all-float groups; deadlines/capacity callbacks also cover validation.
    """
    if any(not math.isfinite(t) or t < 0 for t in (abs_tol, rel_tol)):
        raise ValueError("invalid floating tolerance")
    a, b = Reader(left), Reader(right)
    try:
        if a.header != b.header:
            raise ValueError("metadata/schema/columns mismatch")
        if ordered:
            sentinel = object()
            for x, y in itertools.zip_longest(a, b, fillvalue=sentinel):
                check()
                if x is sentinel or y is sentinel or not equal(x, y, abs_tol, rel_tol):
                    raise ValueError("ordered result mismatch")
            return True
        with tempfile.TemporaryDirectory(prefix="compare-", dir=scratch) as tmp:
            db = sqlite3.connect(str(Path(tmp) / "rows.db"))
            try:
                db.execute("PRAGMA temp_store_directory=" + "'" + tmp.replace("'", "''") + "'")
                progress_errors = []
                def progress():
                    try:
                        check()
                        return 0
                    except BaseException as exc:
                        progress_errors.append(exc)
                        return 1
                db.set_progress_handler(progress, 10000)
                db.executescript("PRAGMA cache_size=-8192; PRAGMA temp_store=FILE;"
                                 "CREATE TABLE a(id INTEGER PRIMARY KEY,k TEXT,v TEXT);"
                                 "CREATE TABLE b(id INTEGER PRIMARY KEY,k TEXT,v TEXT,matched INTEGER);"
                                 "CREATE INDEX bk ON b(k);"
                                 "CREATE TABLE seen(id INTEGER PRIMARY KEY,parent INTEGER,bid INTEGER,done INTEGER);")
                counts = []
                for name, stream in (("a", a), ("b", b)):
                    n = 0
                    for n, row in enumerate(stream, 1):
                        db.execute("INSERT INTO " + name + "(id,k,v) VALUES(?,?,?)", (n, exact_key(row), dumps(row)))
                        if n % 256 == 0:
                            db.commit()
                            check()
                    counts.append(n)
                db.commit()
                if counts[0] != counts[1]:
                    raise ValueError("row count/duplicate loss")
                if abs_tol == rel_tol == 0:
                    diff = db.execute("SELECT v,count(*) FROM a GROUP BY v EXCEPT SELECT v,count(*) FROM b GROUP BY v").fetchone()
                    check()
                    if diff:
                        raise ValueError("multiset value/duplicate mismatch")
                    return True
                for (start,) in db.execute("SELECT id FROM a"):
                    check()
                    db.execute("DELETE FROM seen")
                    db.execute("INSERT INTO seen VALUES(?,NULL,NULL,0)", (start,))
                    found = False
                    while not found:
                        node = db.execute("SELECT id FROM seen WHERE done=0 LIMIT 1").fetchone()
                        if node is None:
                            raise ValueError("tolerant multiset mismatch")
                        node = node[0]
                        db.execute("UPDATE seen SET done=1 WHERE id=?", (node,))
                        key, text = db.execute("SELECT k,v FROM a WHERE id=?", (node,)).fetchone()
                        row = json.loads(text)
                        for bid, other, matched in db.execute("SELECT id,v,matched FROM b WHERE k=?", (key,)):
                            check()
                            if not equal(row, json.loads(other), abs_tol, rel_tol):
                                continue
                            if matched is None:
                                while True:
                                    db.execute("UPDATE b SET matched=? WHERE id=?", (node, bid))
                                    parent, previous = db.execute("SELECT parent,bid FROM seen WHERE id=?", (node,)).fetchone()
                                    if parent is None:
                                        break
                                    node, bid = parent, previous
                                found = True
                                break
                            db.execute("INSERT OR IGNORE INTO seen VALUES(?,?,?,0)", (matched, node, bid))
                    db.commit()
                return True
            except sqlite3.OperationalError as exc:
                if progress_errors:
                    raise progress_errors[0] from exc
                raise
            finally:
                db.close()
    finally:
        a.f.close()
        b.f.close()
