#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
PID_FILE="$ROOT/logs/server.pid"
PORT="$(${PYTHON:-python3} -c "import json; print(json.load(open('$ROOT/config/app.json', encoding='utf-8'))['port'])")"
SYSTEMD_UNIT="${BDPAN_SYSTEMD_UNIT:-baidupan.service}"
if command -v systemctl >/dev/null && systemctl cat "$SYSTEMD_UNIT" >/dev/null 2>&1; then
  systemctl is-active --quiet "$SYSTEMD_UNIT"
  curl -fsS --max-time 3 "http://127.0.0.1:$PORT/api/bootstrap" >/dev/null
  echo "systemd: $SYSTEMD_UNIT HTTP=200"
  exit 0
fi
if [[ ! -f "$PID_FILE" ]]; then echo "状态：未运行"; exit 1; fi
PID="$(cat "$PID_FILE")"
if [[ ! -d "/proc/$PID" ]]; then echo "状态：进程不存在"; exit 1; fi
if curl -fsS --max-time 3 "http://127.0.0.1:$PORT/api/bootstrap" >/dev/null; then
  echo "状态：运行中 PID=$PID HTTP=200"
else
  echo "状态：进程存在但 HTTP 不可用 PID=$PID"; exit 1
fi
