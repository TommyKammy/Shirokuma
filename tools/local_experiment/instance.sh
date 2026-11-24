#!/bin/sh
# Start, stop or check the Workbench (127.0.0.1:8888) and SQL endpoint
# (127.0.0.1:8080) of a local logical_v1 instance.
#
#   tools/local_experiment/instance.sh start  [INSTANCE_DIR]
#   tools/local_experiment/instance.sh stop   [INSTANCE_DIR]
#   tools/local_experiment/instance.sh status [INSTANCE_DIR]
#
# INSTANCE_DIR defaults to $SHIROKUMA_INSTANCE_DIR, then to the newest
# var/shirokuma/local-* directory. Stop uses SIGTERM so both processes clean
# up the port-forwards they own.
set -eu

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
action="${1:-}"
DIR="${2:-${SHIROKUMA_INSTANCE_DIR:-$(ls -d "$ROOT"/var/shirokuma/local-* 2>/dev/null | sort | tail -1)}}"
case "$action" in start|stop|status) ;; *) echo "usage: $0 start|stop|status [INSTANCE_DIR]" >&2; exit 2 ;; esac
[ -n "$DIR" ] && [ -f "$DIR/runtime/runtime-profile.json" ] || { echo "no instance runtime profile under '${DIR:-var/shirokuma/local-*}'" >&2; exit 1; }
DIR="$(cd "$DIR" && pwd)"
mkdir -p "$DIR/logs" "$DIR/run"

running() { [ -f "$DIR/run/$1.pid" ] && kill -0 "$(cat "$DIR/run/$1.pid")" 2>/dev/null; }
listening() { python3 -c "import socket; socket.create_connection(('127.0.0.1', $1), 1)" 2>/dev/null; }
# Both servers bind their port before foundation initialization finishes, so a
# TCP connect is not readiness. Any HTTP response (401 included) means the
# server has entered its request loop.
serving() {
  python3 -c '
import sys, urllib.error, urllib.request
try:
    urllib.request.urlopen("http://127.0.0.1:%s/" % sys.argv[1], timeout=2)
except urllib.error.HTTPError:
    pass
' "$1" 2>/dev/null
}

case "$action" in
status)
  for name in workbench endpoint; do
    if running "$name"; then echo "$name running pid $(cat "$DIR/run/$name.pid")"; else echo "$name stopped"; fi
  done
  ;;
start)
  export SHIROKUMA_RUNTIME_PROFILE="$DIR/runtime/runtime-profile.json"
  export KUBE_CONTEXT="${KUBE_CONTEXT:-colima-mac-studio-solo}"
  export KUBE_NAMESPACE="${KUBE_NAMESPACE:-shirokuma-logical}"
  export PATH="$ROOT/tools/local_experiment/dataops/.venv/bin:$PATH"
  cd "$ROOT"
  for spec in "workbench 8888 workbench_server.py --host 127.0.0.1 --port 8888" \
              "endpoint 8080 warehouse_resident.py endpoint --bind 127.0.0.1 --port 8080"; do
    set -- $spec
    name="$1"; port="$2"; script="$3"; shift 3
    if running "$name"; then echo "$name already running pid $(cat "$DIR/run/$name.pid")"; continue; fi
    # Another instance (or anything else) on the port would make the
    # readiness check below pass against the wrong process.
    if listening "$port"; then
      echo "port $port is already in use by another process; stop it first (another instance?)" >&2
      exit 1
    fi
    nohup python3 "tools/local_experiment/$script" "$@" >"$DIR/logs/$name.log" 2>&1 &
    echo $! >"$DIR/run/$name.pid"
    echo "started $name pid $!"
  done
  for spec in "workbench 8888" "endpoint 8080"; do
    set -- $spec
    i=0
    until serving "$2"; do
      running "$1" || { echo "$1 exited before serving; see $DIR/logs/$1.log" >&2; exit 1; }
      i=$((i + 1)); [ "$i" -lt 120 ] || { echo "$1 did not serve HTTP on $2; see $DIR/logs/$1.log" >&2; exit 1; }
      sleep 1
    done
    running "$1" || { echo "$1 exited; see $DIR/logs/$1.log" >&2; exit 1; }
    echo "$1 serving on 127.0.0.1:$2"
  done
  ;;
stop)
  for name in workbench endpoint; do
    if running "$name"; then kill -TERM "$(cat "$DIR/run/$name.pid")"; echo "stopping $name"; fi
  done
  i=0
  while running workbench || running endpoint; do
    i=$((i + 1)); [ "$i" -lt 30 ] || { echo "still running; check $DIR/run" >&2; exit 1; }
    sleep 1
  done
  rm -f "$DIR/run/workbench.pid" "$DIR/run/endpoint.pid"
  echo stopped
  ;;
esac
