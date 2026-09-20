#!/usr/bin/env python3
"""List cold-verified saved comparisons for explicit prediction token selection.

Rows sort by comparison token, not rank or recency. Partial means one of the
six required receipt files is absent; invalid means its saved contract was not
verified, which does not imply corruption. No coefficients or data are shown.
"""
from __future__ import annotations

from copy import deepcopy
import os
from pathlib import Path
import stat
import sys

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from tools.local_experiment.mlflow import training_runs as R
from tools.local_experiment.notebook_lab import comparison_saved as S

PROFILE = 'saved-comparison-runs-v1'
MAX_CANDIDATES, MAX_PUBLIC_BYTES = 128, 256 * 1024
SCOPE = ('Individually validated saved comparison snapshots, sorted by token; '
         'partial means a required receipt is absent and invalid means not verified; '
         'no model ranking, recency, live source or new quality claim.')
ERROR_CODES = {'COMPARISON_RUNS_' + suffix for suffix in (
    'ARGUMENTS_INVALID', 'ROOT_INVALID', 'INVENTORY_INVALID', 'INVENTORY_CHANGED',
    'ENTRY_LIMIT', 'PUBLIC_LIMIT', 'FAILED', 'INTERRUPTED')}
canonical = R.canonical


class ComparisonRunsError(ValueError):
    """Fixed public reason only."""


def require(condition, code):
    if not condition:
        raise ComparisonRunsError(code)


def _parents():
    require(S.STATE.is_absolute() and S.STATE == S.LAB_STATE / 'comparisons',
            'COMPARISON_RUNS_ROOT_INVALID')
    result = []
    for path in (S.LAB_STATE, *S.LAB_STATE.parents):
        try:
            info = path.lstat()
        except FileNotFoundError:
            result.append((str(path), None))
            continue
        require(stat.S_ISDIR(info.st_mode), 'COMPARISON_RUNS_ROOT_INVALID')
        if path == S.LAB_STATE:
            require(R._owned_directory(info), 'COMPARISON_RUNS_ROOT_INVALID')
        result.append((str(path), info.st_dev, info.st_ino, info.st_mode, info.st_uid))
    return result


def inventory():
    parents = _parents()
    try:
        root = R._scan(S.STATE, MAX_CANDIDATES)
    except R.TrainingRunsError as error:
        code = str(error).replace('TRAINING_RUNS_', 'COMPARISON_RUNS_', 1)
        raise ComparisonRunsError(code if code in ERROR_CODES else 'COMPARISON_RUNS_FAILED') from None
    require(_parents() == parents, 'COMPARISON_RUNS_INVENTORY_CHANGED')
    return dict(parents=parents, root=root)


def _missing_receipt(directory, signature):
    fd, first, missing = None, None, False
    try:
        fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        require(R._signature(os.fstat(fd)) == signature, 'COMPARISON_RUNS_INVENTORY_CHANGED')
        for name in S.FILES:
            try:
                os.stat(name, dir_fd=fd, follow_symlinks=False)
            except FileNotFoundError:
                missing = True
    except BaseException as error:
        first = error
    finally:
        if fd is not None:
            try:
                os.close(fd)
            except BaseException as error:
                first = R._keep(first, error)
    if first is not None:
        raise first
    return missing


def project(saved, token):
    # load_saved has already checked every receipt and its saved source. Keep
    # this projection separate from models/holdout rows and do not refit here.
    require(saved['attempt_token'] == token and saved['result'] == 'passed'
            and saved['cleanup_verified'] is True, 'COMPARISON_RUNS_INVENTORY_INVALID')
    comparison = saved['comparison']
    return dict(comparison_token=token, training_token=comparison['identity']['attempt_token'],
                alpha=comparison['candidate']['alpha'], identity=deepcopy(comparison['identity']),
                lake_source=deepcopy(comparison['lake_source']),
                metrics={name: deepcopy(comparison[name]['metrics']) for name in ('reference', 'candidate')})


def _report(runs, unavailable, *, error_code=None):
    require(error_code is None or error_code in ERROR_CODES, 'COMPARISON_RUNS_FAILED')
    return dict(schema_version=1, profile=PROFILE, result='complete' if error_code is None else 'incomplete',
                runs=runs, unavailable=unavailable,
                counts=dict(candidates=len(runs) + len(unavailable) if error_code is None else None,
                            verified=len(runs), unavailable=len(unavailable)),
                error_code=error_code, scope=SCOPE)


def collect():
    before = inventory()
    runs, unavailable, first = [], [], None
    try:
        entries = {} if before['root'] is None else before['root']['entries']
        for token, signature in sorted(entries.items()):
            valid = (stat.S_ISDIR(signature[2]) and stat.S_IMODE(signature[2]) == 0o700
                     and signature[3] == os.getuid())
            status = 'invalid'
            if valid:
                try:
                    if _missing_receipt(S.STATE / token, signature):
                        status = 'partial'
                    else:
                        runs.append(project(S.load_saved(token), token))
                        continue
                except Exception:
                    pass
            unavailable.append(dict(comparison_token=token, status=status,
                                    reason='COMPARISON_RUN_' + status.upper()))
    except BaseException as error:
        first = error
    finally:
        try:
            require(inventory() == before, 'COMPARISON_RUNS_INVENTORY_CHANGED')
        except BaseException as error:
            first = R._keep(first, error)
    if first is not None:
        raise first
    result = _report(runs, unavailable)
    require(len(canonical(result)) <= MAX_PUBLIC_BYTES, 'COMPARISON_RUNS_PUBLIC_LIMIT')
    return result


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    first = None
    try:
        require(argv == [], 'COMPARISON_RUNS_ARGUMENTS_INVALID')
        result = collect()
    except BaseException as error:
        first = error
        code = str(error) if isinstance(error, ComparisonRunsError) and str(error) in ERROR_CODES else (
            'COMPARISON_RUNS_INTERRUPTED' if not isinstance(error, Exception) else 'COMPARISON_RUNS_FAILED')
        result = _report([], [], error_code=code)
    try:
        print(canonical(result).decode('ascii'), end='')
    except BaseException as error:
        first = R._keep(first, error)
    if first is not None and (not isinstance(first, Exception) or result['result'] == 'complete'):
        raise first
    return 0 if result['result'] == 'complete' else 1


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit(130)
