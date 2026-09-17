#!/usr/bin/env python3
"""Fixed filtered-reader v3 entrypoint; shared snapshot owner and transport."""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from tools.local_experiment.query_filter import contract as C
from tools.local_experiment.query_snapshots import run as SHARED
from tools.local_experiment.query_snapshots import observe as OBSERVE

STATE = ROOT / 'tmp/local-experiment/query-filter'
PROFILE = C.PROFILE
SnapshotError = SHARED.SnapshotError
PUBLIC_CODES = SHARED.codes(C)


def sources():
    return SHARED.sources(C)


class Owner(SHARED.Owner):
    def __init__(self, token, request, output):
        super().__init__(token, request, output, _contract=C)


def observe(request, token, progress, **dependencies):
    return OBSERVE.observe(request, token, progress, _contract=C, **dependencies)


def invoke(token, request, emit, **dependencies):
    return SHARED.invoke(token, request, emit, _contract=C, _state=STATE, **dependencies)


def main(argv=None, *, stdin=None, stdout=None, invoker=None):
    return SHARED.main(argv, stdin=stdin, stdout=stdout, invoker=invoker or invoke,
                       _contract=C, _state=STATE)


if __name__ == '__main__':
    raise SystemExit(main())
