#!/usr/bin/env python3
"""Copy stdin to stdout without exceeding a declared evidence byte limit."""

from __future__ import annotations

import argparse
import sys
from typing import BinaryIO


TRUNCATION_MARKER = b"\n[shirokuma evidence truncated]\n"


def copy_bounded(source: BinaryIO, target: BinaryIO, max_bytes: int) -> bool:
    retained = bytearray()
    truncated = False
    while chunk := source.read(64 * 1024):
        remaining = max_bytes + 1 - len(retained)
        if remaining > 0:
            retained.extend(chunk[:remaining])
        if len(chunk) > max(remaining, 0) or len(retained) > max_bytes:
            truncated = True

    if not truncated:
        target.write(retained)
        return False

    payload_limit = max_bytes - len(TRUNCATION_MARKER)
    target.write(retained[:payload_limit])
    target.write(TRUNCATION_MARKER)
    return True


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--max-bytes", type=int, required=True)
    args = parser.parse_args()
    if args.max_bytes < len(TRUNCATION_MARKER):
        parser.error(f"--max-bytes must be at least {len(TRUNCATION_MARKER)}")
    copy_bounded(sys.stdin.buffer, sys.stdout.buffer, args.max_bytes)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
