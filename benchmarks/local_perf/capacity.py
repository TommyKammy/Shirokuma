"""Campaign-wide physical accounting. Guest files are image contents, not additions."""
import json
import os
import math
from pathlib import Path
import shutil
import stat
import threading
import time
import uuid

from .protocol import GIB, LIMIT, protocol


class CapacitySampler:
    """Serialize scans; ordinary requests coalesce until interval after completion.

    sample returns the callback result for an actual scan, otherwise None.
    Forced requests always scan. Only successful scans defer ordinary requests;
    failures clear the deadline and propagate the callback exception unchanged.
    """
    def __init__(self, interval_s=0.2, clock=None):
        if not math.isfinite(interval_s) or interval_s < 0:
            raise ValueError("finite nonnegative sampling interval required")
        self.interval_s = interval_s
        self.clock = clock if clock is not None else time.monotonic
        self._lock = threading.Lock()
        self._next = None
        self._requests = self._scans = self._failures = 0
        self._seconds = 0.

    def sample(self, callback, *, force=False):
        with self._lock:
            self._requests += 1
            if not force and self._next is not None and self.clock() < self._next:
                return None
            started = self.clock()
            self._scans += 1
            try:
                result = callback()
            except BaseException:
                self._failures += 1
                self._next = None
                raise
            finally:
                completed = self.clock()
                self._seconds += completed - started
            self._next = completed + self.interval_s
            return result

    def snapshot(self):
        with self._lock:
            return dict(version=1, units="seconds", interval_s=self.interval_s,
                        global_requests=self._requests, global_scans=self._scans,
                        global_failed_scans=self._failures, global_scan_s=self._seconds,
                        semantics="serialized scans; interval from successful completion; failures clear deadline; force always scans")


def write_json(path, value):
    path = Path(path)
    tmp = path.with_suffix(path.suffix + ".new")
    with tmp.open("w") as f:
        json.dump(value, f, indent=2, allow_nan=False)
        f.write("\n")
        f.flush()
        os.fsync(f.fileno())
    tmp.replace(path)


def allocated(path):
    """Allocated blocks, not apparent sparse image size; count hardlinks once.

    A fresh directory traversal is performed on every call. Symlinked files are
    counted by their regular-file target, symlinked directories are not
    descended, and any traversal/stat error propagates. The seen set is local to
    this root; accounting is independent per root.
    """
    path = Path(path)
    total, seen = 0, set()

    def count(entry_stat):
        nonlocal total
        key = (entry_stat.st_dev, entry_stat.st_ino)
        if key not in seen:
            total += getattr(entry_stat, "st_blocks", (entry_stat.st_size + 511) // 512) * 512
            seen.add(key)

    def visit(directory):
        with os.scandir(directory) as entries:
            for entry in entries:
                entry_stat = entry.stat(follow_symlinks=False)
                if stat.S_ISREG(entry_stat.st_mode):
                    count(entry_stat)
                elif stat.S_ISDIR(entry_stat.st_mode):
                    visit(entry.path)
                elif entry.is_symlink():
                    target = entry.stat(follow_symlinks=True)
                    if stat.S_ISREG(target.st_mode):
                        count(target)

    if path.is_dir():
        visit(path)
    else:
        entry_stat = path.stat()
        if stat.S_ISREG(entry_stat.st_mode):
            count(entry_stat)
    return total


def guest_requirement(total, free, additional, root=False):
    reserve = max((4 if root else 64) * GIB, int(total * .1))
    return {"total_bytes": total, "free_bytes": free, "additional_bytes": additional,
            "reserve_bytes": reserve, "ready": free >= additional + reserve,
            "shortfall_bytes": max(0, additional + reserve - free)}


class Campaign:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.path = self.root / "campaign.json"
        self.state = json.loads(self.path.read_text())
        if self.state.get("format") != 1 or self.state.get("root") != str(self.root):
            raise ValueError("not this owned campaign")

    @classmethod
    def create(cls, root, images=(), host_paths=(), drain_bytes=64 * 1024 ** 2):
        root = Path(root).resolve()
        repo = Path(__file__).resolve().parents[2]
        if root == repo or repo in root.parents:
            raise ValueError("campaign output must be outside Git checkout")
        if drain_bytes < 0:
            raise ValueError("negative drain reserve")
        roots = [root] + [Path(x).resolve() for x in (*images, *host_paths)]
        for i, a in enumerate(roots):
            for b in roots[i + 1:]:
                if a == b or a in b.parents or b in a.parents:
                    raise ValueError("overlapping host accounting paths")
        if any(not p.exists() for p in roots[1:]):
            raise ValueError("accounted image/path missing")
        ancestor = root.parent
        while not ancestor.exists():
            ancestor = ancestor.parent
        if any(p.stat().st_dev != ancestor.stat().st_dev for p in roots[1:]):
            raise ValueError("unsupported multi-volume accounting; all paths must share one host filesystem")
        root.mkdir(parents=True, exist_ok=False)
        state = {"format": 1, "id": str(uuid.uuid4()), "root": str(root),
                 "protocol": protocol(), "baseline_host_free": shutil.disk_usage(root).free,
                 "baseline": {str(p): allocated(p) for p in roots}, "owned": {},
                 "drain_bytes": drain_bytes}
        write_json(root / "campaign.json", state)
        return cls(root)

    def refresh(self):
        self.state = json.loads(self.path.read_text())

    def own(self, name, kind):
        self.refresh()
        if not name or Path(name).name != name or name in (".", ".."):
            raise ValueError("single fresh output name required")
        path = self.root / name
        if name in self.state["owned"] or path.exists():
            raise FileExistsError("output already used; attempts cannot be overwritten")
        self.state["owned"][name] = {"kind": kind, "status": "INCOMPLETE"}
        write_json(self.path, self.state)
        path.mkdir()
        return path

    def mark(self, path, status):
        self.refresh()
        name = Path(path).name
        if Path(path).resolve().parent != self.root or name not in self.state["owned"]:
            raise ValueError("unowned output")
        self.state["owned"][name]["status"] = status
        write_json(self.path, self.state)

    def cleanup(self, name):
        self.refresh()
        if name not in self.state["owned"]:
            raise ValueError("refusing unowned cleanup")
        entry = self.state["owned"][name]
        if entry["kind"] not in ("dataset", "comparison", "ingest-preparation") or entry["status"] != "SUCCESS":
            raise ValueError("retain receipt/proof kinds and incomplete/failed evidence")
        shutil.rmtree(self.root / name)
        self.mark(self.root / name, "CLEANED")

    def check(self, additional=0, snapshot=None):
        """additional is maximum remaining allocation, including copy/export/sort.

        snapshot permits external sampling/tests; it never changes the baseline.
        Supply allocated map for exactly the campaign's host paths, not guest files.
        """
        if additional < 0:
            raise ValueError("negative additional peak")
        self.refresh()
        base = self.state["baseline"]
        if any(Path(p).stat().st_dev != self.root.stat().st_dev for p in base):
            raise ValueError("unsupported multi-volume accounting; all paths must share one host filesystem")
        if snapshot is None:
            current = {p: allocated(p) for p in base}
            free = shutil.disk_usage(self.root).free
        else:
            current, free = snapshot["allocated"], snapshot["host_free"]
            if set(current) != set(base) or any(v < 0 for v in current.values()) or free < 0:
                raise ValueError("incomplete capacity snapshot")
        # No negative credits if an old image shrinks; retained snapshots must be
        # included by the caller as disjoint host paths at campaign creation.
        used = sum(max(0, current[p] - n) for p, n in base.items())
        reserve = self.state["protocol"]["host_reserve_bytes"]
        drain = self.state["drain_bytes"]
        ceiling = min(LIMIT, self.state["baseline_host_free"] - reserve)
        report = dict(used_bytes=used, additional_bytes=additional, drain_bytes=drain,
                      host_free_bytes=free, baseline_ceiling_bytes=ceiling,
                      remaining_bytes=min(ceiling - used, free - reserve) - drain)
        if used + additional + drain > ceiling or additional + drain > free - reserve:
            raise RuntimeError("capacity reserve/2TB ceiling would be exceeded")
        return report
