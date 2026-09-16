#!/usr/bin/env python3
"""Render the local Trino deployment with an explicitly imported image digest."""

import argparse
import re
import subprocess
import sys
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("digest", help="Imported k3s image manifest digest (sha256:...)")
    args = parser.parse_args()
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", args.digest):
        parser.error("digest must be sha256 followed by 64 lowercase hexadecimal digits")
    if args.digest == "sha256:" + "0" * 64:
        parser.error("the all-zero test digest is not a runnable image")
    root = Path(__file__).resolve().parents[3]
    result = subprocess.run(
        ["kubectl", "kustomize", str(root / "deploy/local-experiment/trino")],
        check=True, capture_output=True, text=True,
    )
    placeholder = "sha256:REPLACE_WITH_IMPORTED_LOCAL_MANIFEST_DIGEST"
    if result.stdout.count(placeholder) != 1:
        raise SystemExit("expected exactly one image digest placeholder")
    sys.stdout.write(result.stdout.replace(placeholder, args.digest))


if __name__ == "__main__":
    main()
