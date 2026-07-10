#!/usr/bin/env python3
"""Fail closed unless every GitOps bootstrap image is admitted by policy."""

from __future__ import annotations

import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CANDIDATES = ROOT / "opentofu/dev/bootstrap-images.json"
LEDGER = ROOT / "security/resident-images.json"
DIGEST_REFERENCE = re.compile(r"^[^:@\s]+(?:/[^:@\s]+)+@sha256:[0-9a-f]{64}$")


def load_json(path: Path) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"cannot read trusted JSON input {path.relative_to(ROOT)}: {error}") from error


def main() -> int:
    try:
        candidates = load_json(CANDIDATES)
        ledger = load_json(LEDGER)
    except ValueError as error:
        print(f"gitops-image-admission: {error}")
        return 1

    if not isinstance(candidates, dict) or not isinstance(ledger, dict):
        print("gitops-image-admission: candidate and ledger roots must be objects")
        return 1
    images = ledger.get("images")
    if not isinstance(images, list):
        print("gitops-image-admission: resident image ledger is malformed")
        return 1

    admitted = {
        image.get("reference")
        for image in images
        if isinstance(image, dict) and isinstance(image.get("reference"), str)
    }
    errors: list[str] = []
    for component, candidate in candidates.items():
        if not isinstance(candidate, dict):
            errors.append(f"{component}: candidate must be an object")
            continue
        reference = candidate.get("reference")
        tag = candidate.get("tag")
        if not isinstance(reference, str) or not DIGEST_REFERENCE.fullmatch(reference):
            errors.append(f"{component}: exact repository@sha256 reference is required")
            continue
        if not isinstance(tag, str) or f"@{reference.rsplit('@', 1)[1]}" not in tag:
            errors.append(f"{component}: deployed tag must carry the admitted digest")
        if reference not in admitted:
            errors.append(f"{component}: {reference} is not admitted by security/resident-images.json")

    if errors:
        for error in errors:
            print(f"gitops-image-admission: blocked: {error}")
        return 1

    print(f"gitops-image-admission: ok images={len(candidates)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
