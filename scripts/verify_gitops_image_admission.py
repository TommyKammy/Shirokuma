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
CUSTOMIZATION = ROOT / "deploy/gitops/clusters/local-lite/flux-system/kustomization.yaml"
DIGEST_REFERENCE = re.compile(r"^[^:@\s]+(?:/[^:@\s]+)+@sha256:[0-9a-f]{64}$")
DEPLOYED_TAG = re.compile(r"^[^@\s]+@sha256:[0-9a-f]{64}$")
PATCH_BLOCK = re.compile(
    r"^  - patch: \|\n(?P<patch>(?: {6}.*\n)+)"
    r" {4}target:\n(?P<target>(?: {6}.*(?:\n|$))+)",
    re.MULTILINE,
)


def load_json(path: Path) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        try:
            display_path = path.relative_to(ROOT)
        except ValueError:
            display_path = path
        raise ValueError(f"cannot read trusted JSON input {display_path}: {error}") from error


def load_customized_images(path: Path) -> dict[str, str]:
    try:
        content = path.read_text(encoding="utf-8")
    except OSError as error:
        raise ValueError(f"cannot read Flux bootstrap customization {path}: {error}") from error

    images: dict[str, str] = {}
    for match in PATCH_BLOCK.finditer(content):
        patch = match.group("patch")
        target = match.group("target")
        image_path = re.search(
            r"^\s*path:\s*/spec/template/spec/containers/0/image\s*$",
            patch,
            re.MULTILINE,
        )
        value = re.search(r"^\s*value:\s*(\S+)\s*$", patch, re.MULTILINE)
        kind = re.search(r"^\s*kind:\s*(\S+)\s*$", target, re.MULTILINE)
        name = re.search(r"^\s*name:\s*(\S+)\s*$", target, re.MULTILINE)
        if not image_path or not value or not kind or kind.group(1) != "Deployment" or not name:
            continue
        component = name.group(1)
        if component in images:
            raise ValueError(f"duplicate Flux image customization for {component}")
        images[component] = value.group(1)
    return images


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Verify that every deployed GitOps bootstrap image is admitted."
    )
    parser.add_argument("--candidates", type=Path, default=CANDIDATES)
    parser.add_argument("--ledger", type=Path, default=LEDGER)
    parser.add_argument("--customization", type=Path, default=CUSTOMIZATION)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        candidates = load_json(args.candidates)
        ledger = load_json(args.ledger)
        customized_images = load_customized_images(args.customization)
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
        customized_reference = customized_images.get(component)
        if customized_reference != deployed_reference:
            errors.append(
                f"{component}: bootstrap customization deploys {customized_reference!r}, "
                f"expected {deployed_reference}"
            )
        if deployed_reference not in admitted:
            errors.append(
                f"{component}: deployed image {deployed_reference} is not admitted by "
                f"{args.ledger}"
            )

    unexpected_customizations = sorted(set(customized_images) - set(candidates))
    if unexpected_customizations:
        errors.append(
            "bootstrap customization has unexpected controller images: "
            + ", ".join(unexpected_customizations)
        )

    if errors:
        for error in errors:
            print(f"gitops-image-admission: blocked: {error}")
        return 1

    print(f"gitops-image-admission: ok images={len(candidates)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
