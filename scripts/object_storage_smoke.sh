#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
KUBE_CONTEXT="${KUBE_CONTEXT:-colima-mac-studio-solo}"
KUBE_NAMESPACE="${KUBE_NAMESPACE:-shirokuma-storage}"
S3_SERVICE="${S3_SERVICE:-seaweedfs-s3}"
S3_LOCAL_PORT="${S3_LOCAL_PORT:-18333}"
S3_SMOKE_BUCKET="${S3_SMOKE_BUCKET:-shirokuma-smoke-$(date -u +%Y%m%d%H%M%S)-$$}"
PORT_FORWARD_PID=""
PORT_FORWARD_LOG=""

cleanup() {
  if [[ -n "${PORT_FORWARD_PID}" ]]; then
    kill "${PORT_FORWARD_PID}" >/dev/null 2>&1 || true
    wait "${PORT_FORWARD_PID}" >/dev/null 2>&1 || true
  fi
  if [[ -n "${PORT_FORWARD_LOG}" ]]; then
    rm -f "${PORT_FORWARD_LOG}"
  fi
}
trap cleanup EXIT

command -v python3 >/dev/null || {
  echo "object-storage-smoke: python3 is required" >&2
  exit 1
}

if [[ -z "${S3_ENDPOINT:-}" ]]; then
  command -v kubectl >/dev/null || {
    echo "object-storage-smoke: kubectl is required when S3_ENDPOINT is unset" >&2
    exit 1
  }
  PORT_FORWARD_LOG="$(mktemp)"
  kubectl --context "${KUBE_CONTEXT}" -n "${KUBE_NAMESPACE}" \
    port-forward --address 127.0.0.1 "service/${S3_SERVICE}" \
    "${S3_LOCAL_PORT}:8333" >"${PORT_FORWARD_LOG}" 2>&1 &
  PORT_FORWARD_PID=$!
  export S3_ENDPOINT="http://127.0.0.1:${S3_LOCAL_PORT}"

  ready=0
  for _ in {1..60}; do
    if ! kill -0 "${PORT_FORWARD_PID}" >/dev/null 2>&1; then
      echo "object-storage-smoke: port-forward exited before readiness" >&2
      exit 1
    fi
    if python3 -c \
      'import socket,sys; s=socket.create_connection(("127.0.0.1", int(sys.argv[1])), 1); s.close()' \
      "${S3_LOCAL_PORT}" >/dev/null 2>&1; then
      ready=1
      break
    fi
    sleep 1
  done
  if [[ "${ready}" != 1 ]]; then
    echo "object-storage-smoke: S3 port did not become ready" >&2
    exit 1
  fi
fi

python3 "${ROOT}/scripts/object_storage_s3.py" smoke \
  --bucket "${S3_SMOKE_BUCKET}"
