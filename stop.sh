#!/usr/bin/env bash
set -euo pipefail
ROOT="${BDPAN_ROOT:-$(cd "$(dirname "$0")" && pwd)}"
PID_FILE="$ROOT/logs/server.pid"
SYSTEMD_UNIT="${BDPAN_SYSTEMD_UNIT:-baidupan.service}"
if command -v systemctl >/dev/null && systemctl cat "$SYSTEMD_UNIT" >/dev/null 2>&1; then
  sudo -n systemctl stop "$SYSTEMD_UNIT"
  rm -f "$PID_FILE"
  exit 0
fi
if [[ ! -f "$PID_FILE" ]]; then echo "服务未运行"; exit 0; fi
PID="$(cat "$PID_FILE")"
if kill -0 "$PID" 2>/dev/null; then kill "$PID"; echo "服务已停止，PID=$PID"; else echo "PID 文件已过期"; fi
rm -f "$PID_FILE"
