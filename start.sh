#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
mkdir -p "$ROOT/logs" "$ROOT/data"
PID_FILE="$ROOT/logs/server.pid"
if [[ -f "$PID_FILE" ]] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
  echo "服务已运行，PID=$(cat "$PID_FILE")"
  exit 0
fi
rm -f "$PID_FILE"
export PYTHONPATH="$ROOT/src/app${PYTHONPATH:+:$PYTHONPATH}"
nohup "${PYTHON:-python3}" -m bdpan.web --config "$ROOT/config/app.json" \
  >>"$ROOT/logs/server.stdout.log" 2>>"$ROOT/logs/server.stderr.log" &
echo $! > "$PID_FILE"
echo "服务已启动，PID=$!"
