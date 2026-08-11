#!/bin/bash
# Lychee 一键启动（带看门狗：后端崩溃自动重启）
#
# 用法：
#   scripts/start.sh            前台运行看门狗（终端会持续打印日志，Ctrl+C 停止）
#   scripts/start.sh >/dev/null 2>&1 &   后台运行
#   scripts/stop.sh             停止
#
# 环境变量：
#   PYTHON         Python 解释器路径（默认项目 conda 环境）
#   LYCHEE_HOST    监听地址（默认 127.0.0.1）
#   LYCHEE_PORT    监听端口（默认 8000）
set -u
cd "$(dirname "$0")/.."

PY="${PYTHON:-/Users/rose327/miniconda3/envs/ai/bin/python}"
HOST="${LYCHEE_HOST:-127.0.0.1}"
PORT="${LYCHEE_PORT:-8000}"
LOG_DIR="logs"
PID_FILE="$LOG_DIR/lychee.pid"
WATCHDOG_PID_FILE="$LOG_DIR/lychee.watchdog.pid"
STOP_FLAG="$LOG_DIR/.stop"
mkdir -p "$LOG_DIR"

echo "==> 检查 Ollama（VLM 画面理解依赖）..."
if ! pgrep -x ollama >/dev/null; then
  echo "    Ollama 未运行，正在后台启动..."
  nohup ollama serve >/dev/null 2>&1 &
  sleep 2
fi
if ollama list 2>/dev/null | grep -q "qwen2.5vl"; then
  echo "    VLM 模型已就绪"
else
  echo "    [警告] 未检测到 qwen2.5vl 模型，画面描述功能不可用。执行: ollama pull qwen2.5vl:3b"
fi

# 若已存在 pid 文件且进程存活，提示已运行
if [ -f "$PID_FILE" ] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
  echo "==> Lychee 已在运行（PID $(cat "$PID_FILE")），访问 http://$HOST:$PORT/"
  exit 0
fi

rm -f "$STOP_FLAG"
echo "$$" > "$WATCHDOG_PID_FILE"
echo "==> 启动 Lychee 后端（看门狗守护，崩溃自动重启）..."
echo "    访问: http://$HOST:$PORT/"

cleanup() {
  echo "$(date) 收到停止信号，关闭看门狗与后端" >> "$LOG_DIR/server.out"
  [ -f "$PID_FILE" ] && kill "$(cat "$PID_FILE")" 2>/dev/null
  rm -f "$WATCHDOG_PID_FILE" "$STOP_FLAG"
  exit 0
}
trap cleanup INT TERM

while true; do
  if [ -f "$STOP_FLAG" ]; then
    echo "$(date) 收到停止标记，退出看门狗" >> "$LOG_DIR/server.out"
    break
  fi
  echo "$(date) 启动 uvicorn (host=$HOST port=$PORT)..." >> "$LOG_DIR/server.out"
  "$PY" -m uvicorn src.api.server:app --host "$HOST" --port "$PORT" >> "$LOG_DIR/server.out" 2>&1 &
  UV_PID=$!
  echo "$UV_PID" > "$PID_FILE"
  wait "$UV_PID"
  code=$?
  echo "$(date) uvicorn 退出 (code $code)，3 秒后重启..." >> "$LOG_DIR/server.out"
  sleep 3
done