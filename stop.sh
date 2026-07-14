#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
PID_FILE="$ROOT/logs/server.pid"
if [[ ! -f "$PID_FILE" ]]; then echo "服务未运行"; exit 0; fi
PID="$(cat "$PID_FILE")"
if kill -0 "$PID" 2>/dev/null; then kill "$PID"; echo "服务已停止，PID=$PID"; else echo "PID 文件已过期"; fi
rm -f "$PID_FILE"
