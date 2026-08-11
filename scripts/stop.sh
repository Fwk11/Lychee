#!/bin/bash
# 停止 Lychee 后端（看门狗 + uvicorn 一并关闭）
set -u
cd "$(dirname "$0")/.."

PID_FILE="logs/lychee.pid"
WATCHDOG_PID_FILE="logs/lychee.watchdog.pid"
STOP_FLAG="logs/.stop"

# 通知看门狗停止循环
touch "$STOP_FLAG"

if [ -f "$PID_FILE" ]; then
  PID="$(cat "$PID_FILE")"
  if kill -0 "$PID" 2>/dev/null; then
    echo "==> 正在停止 Lychee 后端 (PID $PID)..."
    kill "$PID" 2>/dev/null
    # 等最多 5 秒优雅退出
    for i in $(seq 1 5); do
      kill -0 "$PID" 2>/dev/null || break
      sleep 1
    done
    kill -9 "$PID" 2>/dev/null
  fi
  rm -f "$PID_FILE"
fi

if [ -f "$WATCHDOG_PID_FILE" ]; then
  WPID="$(cat "$WATCHDOG_PID_FILE")"
  kill -0 "$WPID" 2>/dev/null && kill "$WPID" 2>/dev/null
  rm -f "$WATCHDOG_PID_FILE"
fi

rm -f "$STOP_FLAG"
echo "==> Lychee 已停止"