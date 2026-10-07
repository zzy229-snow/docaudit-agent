#!/usr/bin/env bash
# 一键启动脚本(PRD §17.2 交付物:一键启动脚本 / §22.3 端口约定)
#
# 用法:
#   bash scripts/start.sh            # 创建/复用 .venv,安装依赖,启动 API + 工作台
#   API_PORT=8101 WEB_PORT=8501 bash scripts/start.sh    # 自定义端口(多人并行开发)
#
# 日志:logs/api.log、logs/web.log;停止:bash scripts/stop.sh
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

API_PORT="${API_PORT:-8000}"
WEB_PORT="${WEB_PORT:-8500}"
PYTHON_BIN="${PYTHON_BIN:-python}"

if [ ! -d ".venv" ]; then
  echo "[start] 创建虚拟环境 .venv"
  "$PYTHON_BIN" -m venv .venv
fi

if [ -x ".venv/Scripts/python.exe" ]; then
  VENV_PY=".venv/Scripts/python.exe"      # Windows (git-bash)
else
  VENV_PY=".venv/bin/python"
fi

echo "[start] 安装依赖(requirements.txt)"
"$VENV_PY" -m pip install --upgrade pip >/dev/null
"$VENV_PY" -m pip install -r requirements.txt

if [ ! -f ".env" ] && [ -f ".env.example" ]; then
  echo "[start] 未发现 .env,从 .env.example 复制(默认 mock 模式,不需要密钥)"
  cp .env.example .env
fi

mkdir -p logs

echo "[start] 启动 API http://127.0.0.1:${API_PORT}/docs"
"$VENV_PY" -m uvicorn app.api.main:app --host 127.0.0.1 --port "$API_PORT" > logs/api.log 2>&1 &
echo $! > logs/api.pid

echo "[start] 启动审核工作台 http://127.0.0.1:${WEB_PORT}"
"$VENV_PY" -m streamlit run streamlit_app.py \
  --server.port "$WEB_PORT" --server.address 127.0.0.1 --server.headless true > logs/web.log 2>&1 &
echo $! > logs/web.pid

sleep 3
echo "[start] 就绪检查:"
"$VENV_PY" - <<PY
import json, urllib.request
try:
    with urllib.request.urlopen("http://127.0.0.1:${API_PORT}/health", timeout=5) as resp:
        print("  API /health ->", json.load(resp))
except Exception as exc:  # noqa: BLE001
    print("  API 尚未就绪,请查看 logs/api.log:", exc)
PY
echo "[start] 完成。日志:logs/api.log、logs/web.log;停止:bash scripts/stop.sh"
