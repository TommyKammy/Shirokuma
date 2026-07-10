#!/bin/sh
set -eu

PROFILE="mac-studio-solo"
CONTEXT="colima-${PROFILE}"
COLIMA_BIN="${COLIMA_BIN:-colima}"
KUBECTL_BIN="${KUBECTL_BIN:-kubectl}"
HELM_BIN="${HELM_BIN:-helm}"
DOCKER_BIN="${DOCKER_BIN:-docker}"

usage() {
  cat <<'EOF'
Usage: scripts/colima_baseline.sh <start|status|reset>

  start   Start the accepted solo-lite Colima built-in k3s profile and verify it.
  status  Fail unless the VZ/aarch64 VM, Ready arm64 node, and Helm access verify.
  reset   Delete and rebuild the profile; requires --confirm-data-loss.
EOF
}

die() {
  printf 'colima-baseline: %s\n' "$*" >&2
  exit 1
}

require_tool() {
  command -v "$1" >/dev/null 2>&1 || die "required tool not found: $1"
}

require_tools() {
  require_tool "$COLIMA_BIN"
  require_tool "$KUBECTL_BIN"
  require_tool "$HELM_BIN"
  require_tool "$DOCKER_BIN"
}

start_profile() {
  "$COLIMA_BIN" start \
    --profile "$PROFILE" \
    --vm-type=vz \
    --arch aarch64 \
    --cpu 16 \
    --memory 96 \
    --disk 400 \
    --kubernetes \
    --runtime docker \
    --binfmt=false \
    --activate=false
}

capture_current_context() {
  original_context=$("$KUBECTL_BIN" config current-context 2>/dev/null || true)
  original_docker_context=$("$DOCKER_BIN" context show 2>/dev/null || true)
}

restore_current_context() {
  if [ -n "$original_context" ]; then
    "$KUBECTL_BIN" config use-context "$original_context" >/dev/null
  else
    "$KUBECTL_BIN" config unset current-context >/dev/null 2>&1 || true
  fi
  if [ -n "$original_docker_context" ]; then
    "$DOCKER_BIN" context use "$original_docker_context" >/dev/null
  fi
}

require_status_field() {
  field=$1
  expected=$2
  case "$status_json" in
    *"\"${field}\":${expected},"*|*"\"${field}\":${expected}}"*) ;;
    *) die "Colima profile does not match baseline: expected ${field}=${expected}" ;;
  esac
}

verify_status() {
  status_json=$("$COLIMA_BIN" status --profile "$PROFILE" --json)
  "$COLIMA_BIN" list --json

  require_status_field driver '"macOS Virtualization.Framework"'
  require_status_field arch '"aarch64"'
  require_status_field runtime '"docker"'
  require_status_field kubernetes true
  require_status_field cpu 16
  require_status_field memory 103079215104
  require_status_field disk 429496729600

  vm_arch=$("$COLIMA_BIN" ssh --profile "$PROFILE" -- uname -m)
  [ "$vm_arch" = "aarch64" ] || die "expected VM architecture aarch64, got: ${vm_arch:-missing}"

  foreign_binfmt=$("$COLIMA_BIN" ssh --profile "$PROFILE" -- sh -c '
    for handler in /proc/sys/fs/binfmt_misc/qemu-* /proc/sys/fs/binfmt_misc/rosetta; do
      [ ! -e "$handler" ] || basename "$handler"
    done
  ')
  [ -z "$foreign_binfmt" ] || \
    die "foreign architecture emulation is enabled: $foreign_binfmt"

  data_disk_bytes=$("$COLIMA_BIN" ssh --profile "$PROFILE" -- sh -c '
    source=$(findmnt -n -o SOURCE /var/lib/docker) || exit 1
    source=${source%%[*}
    parent=$(lsblk -nro PKNAME "$source") || exit 1
    [ -n "$parent" ] || exit 1
    lsblk -bdnro SIZE "/dev/$parent"
  ')
  [ "$data_disk_bytes" = "429496729600" ] || \
    die "Colima data disk does not match baseline: expected 429496729600 bytes, got ${data_disk_bytes:-missing}"

  "$KUBECTL_BIN" --context "$CONTEXT" cluster-info

  node_arches=$("$KUBECTL_BIN" --context "$CONTEXT" get nodes \
    -o 'jsonpath={range .items[*]}{.metadata.name}{"="}{.status.nodeInfo.architecture}{"\n"}{end}')
  [ -n "$node_arches" ] || die "expected at least one Kubernetes node"
  printf '%s\n' "$node_arches" | while IFS='=' read -r node arch; do
    [ -n "$node" ] || die "node name is missing"
    [ "$arch" = "arm64" ] || die "expected arm64 node, got ${node}=${arch:-missing}"
  done

  node_readiness=$("$KUBECTL_BIN" --context "$CONTEXT" get nodes \
    -o 'jsonpath={range .items[*]}{.metadata.name}{"="}{range .status.conditions[?(@.type=="Ready")]}{.status}{end}{"\n"}{end}')
  [ -n "$node_readiness" ] || die "expected Kubernetes Ready conditions"
  printf '%s\n' "$node_readiness" | while IFS='=' read -r node ready; do
    [ -n "$node" ] || die "node name is missing"
    [ "$ready" = "True" ] || die "expected Ready node, got ${node}=${ready:-missing}"
  done

  "$KUBECTL_BIN" --context "$CONTEXT" get nodes -o wide
  "$HELM_BIN" version
  "$HELM_BIN" list --kube-context "$CONTEXT" --all-namespaces >/dev/null
  printf 'Colima baseline ready: profile=%s context=%s vm=aarch64 nodes=arm64/Ready helm=reachable\n' \
    "$PROFILE" "$CONTEXT"
}

main() {
  command_name="${1:-}"
  case "$command_name" in
    start)
      [ "$#" -eq 1 ] || { usage >&2; exit 2; }
      require_tools
      capture_current_context
      start_profile
      restore_current_context
      verify_status
      ;;
    status)
      [ "$#" -eq 1 ] || { usage >&2; exit 2; }
      require_tools
      verify_status
      ;;
    reset)
      [ "$#" -eq 2 ] || { usage >&2; exit 2; }
      [ "$2" = "--confirm-data-loss" ] || die "reset requires --confirm-data-loss"
      require_tools
      capture_current_context
      "$COLIMA_BIN" stop --profile "$PROFILE" --force
      "$COLIMA_BIN" delete --profile "$PROFILE" --data --force
      start_profile
      restore_current_context
      verify_status
      ;;
    -h|--help)
      usage
      ;;
    *)
      usage >&2
      exit 2
      ;;
  esac
}

main "$@"
