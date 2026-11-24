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
  for spec in "workbench workbench_server.py --host 127.0.0.1 --port 8888" \
              "endpoint warehouse_resident.py endpoint --bind 127.0.0.1 --port 8080"; do
    set -- $spec
    name="$1"; script="$2"; shift 2
    if running "$name"; then echo "$name already running pid $(cat "$DIR/run/$name.pid")"; continue; fi
    nohup python3 "tools/local_experiment/$script" "$@" >"$DIR/logs/$name.log" 2>&1 &
    echo $! >"$DIR/run/$name.pid"
    echo "started $name pid $!"
  done
  for port in 8888 8080; do
    i=0
    until python3 -c "import socket; socket.create_connection(('127.0.0.1', $port), 1)" 2>/dev/null; do
      i=$((i + 1)); [ "$i" -lt 180 ] || { echo "port $port did not listen; see $DIR/logs" >&2; exit 1; }
      sleep 0.5
    done
    echo "listening on 127.0.0.1:$port"
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
