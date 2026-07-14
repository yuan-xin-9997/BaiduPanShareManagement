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
PID=$!

PORT="$("${PYTHON:-python3}" -c "import json; print(json.load(open('$ROOT/config/app.json', encoding='utf-8'))['port'])")"
for _ in {1..20}; do
  if ! kill -0 "$PID" 2>/dev/null; then
    echo "服务进程启动后退出，PID=$PID" >&2
    tail -n 50 "$ROOT/logs/server.stderr.log" >&2 || true
    rm -f "$PID_FILE"
    exit 1
  fi
  if curl -fsS --max-time 2 "http://127.0.0.1:$PORT/api/bootstrap" >/dev/null; then
    echo "服务已启动，PID=$PID HTTP=200"
    exit 0
  fi
  sleep 1
done

echo "服务启动超时，PID=$PID，端口=$PORT" >&2
tail -n 50 "$ROOT/logs/server.stderr.log" >&2 || true
kill "$PID" 2>/dev/null || true
rm -f "$PID_FILE"
exit 1
