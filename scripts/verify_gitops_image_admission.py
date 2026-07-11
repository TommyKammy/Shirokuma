#!/usr/bin/env python3
"""Fail closed unless every GitOps bootstrap image is admitted by policy."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CANDIDATES = ROOT / "opentofu/dev/bootstrap-images.json"
LEDGER = ROOT / "security/resident-images.json"
DIGEST_REFERENCE = re.compile(r"^[^:@\s]+(?:/[^:@\s]+)+@sha256:[0-9a-f]{64}$")
DEPLOYED_TAG = re.compile(r"^[^@\s]+@sha256:[0-9a-f]{64}$")


def load_json(path: Path) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        try:
            display_path = path.relative_to(ROOT)
        except ValueError:
            display_path = path
        raise ValueError(f"cannot read trusted JSON input {display_path}: {error}") from error


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Verify that every deployed GitOps bootstrap image is admitted."
    )
    parser.add_argument("--candidates", type=Path, default=CANDIDATES)
    parser.add_argument("--ledger", type=Path, default=LEDGER)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        candidates = load_json(args.candidates)
        ledger = load_json(args.ledger)
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
        repository = candidate.get("repository")
        tag = candidate.get("tag")
        if not isinstance(reference, str) or not DIGEST_REFERENCE.fullmatch(reference):
            errors.append(f"{component}: exact repository@sha256 reference is required")
            continue
        if not isinstance(repository, str) or not repository or "@" in repository:
            errors.append(f"{component}: deployed repository must be an untagged image repository")
            continue
        if not isinstance(tag, str) or not DEPLOYED_TAG.fullmatch(tag):
            errors.append(f"{component}: deployed tag must include an exact sha256 digest")
            continue

        digest = tag.rsplit("@", 1)[1]
        deployed_reference = f"{repository}@{digest}"
        if reference != deployed_reference:
            errors.append(
                f"{component}: declared reference {reference} does not match deployed image "
                f"{deployed_reference}"
            )
        if deployed_reference not in admitted:
            errors.append(
                f"{component}: deployed image {deployed_reference} is not admitted by "
                f"{args.ledger}"
            )

    if errors:
        for error in errors:
            print(f"gitops-image-admission: blocked: {error}")
        return 1

    print(f"gitops-image-admission: ok images={len(candidates)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
