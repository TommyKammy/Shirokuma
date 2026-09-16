#!/usr/bin/env bash
set -euo pipefail

if [ "$#" -lt 1 ] || [ "$#" -gt 2 ]; then
  echo "usage: $0 /absolute/path/trino-server-483.tar.gz [local-image-tag]" >&2
  exit 2
fi

archive="$1"
image="${2:-shirokuma.local/trino:483-experiment}"
expected=4f3978428f26f36398c94b85a3e03b5301394919c8a4271b497b0fcd1698d0cb
actual=$(shasum -a 256 "$archive" | awk '{print $1}')
if [ "$actual" != "$expected" ]; then
  echo "Trino 483 upstream archive SHA-256 mismatch" >&2
  exit 1
fi

script_dir=$(cd -- "$(dirname -- "$0")" && pwd)
stage=$(mktemp -d "${TMPDIR:-/tmp}/shirokuma-trino-local.XXXXXX")
trap 'rm -rf "$stage"' EXIT
mkdir -p "$stage/unpacked" "$stage/context/trino-server-483/plugin"
# Iceberg JARs are hard links to entries in other plugin directories. Extract
# the complete verified archive before selecting directories, so tar can resolve
# every target. Moving selected files preserves their regular-file inodes after
# the unselected hard links are removed.
tar -xzf "$archive" -C "$stage/unpacked"
distribution="$stage/unpacked/trino-server-483"
mv "$distribution/bin" "$distribution/lib" "$stage/context/trino-server-483/"
mv "$distribution/plugin/iceberg" "$stage/context/trino-server-483/plugin/"
cp "$distribution/NOTICE" "$stage/context/trino-server-483/NOTICE"
rm -rf "$stage/unpacked"
cp "$script_dir/Dockerfile" "$stage/context/Dockerfile"
python3 - "$stage/context/trino-server-483" <<'PY'
import sys
from pathlib import Path
root = Path(sys.argv[1])
if not (root / "bin/linux-arm64/launcher").is_file():
    raise SystemExit("missing linux/arm64 launcher")
for path in root.rglob("*"):
    if path.is_symlink() and not path.exists():
        raise SystemExit("unresolved archive link: " + str(path.relative_to(root)))
jars = list((root / "plugin/iceberg").glob("*.jar"))
if not jars or any(not path.is_file() or path.stat().st_size == 0 for path in jars):
    raise SystemExit("missing or empty Iceberg connector JAR")
print(f"Prepared local context: {len(jars)} Iceberg JARs; ARM64 launcher present")
PY
if [ "${SHIROKUMA_TRINO_VALIDATE_ONLY:-0}" = 1 ]; then
  echo "Archive extraction validated; Docker build skipped."
  exit 0
fi
docker --context colima-mac-studio-solo build --platform linux/arm64 --tag "$image" "$stage/context"
printf 'Built local-only image: %s\nNo registry push was performed.\n' "$image"
