#!/usr/bin/env bash
# 停止 scripts/start.sh 启动的服务
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

for name in api web; do
  pid_file="logs/${name}.pid"
  if [ -f "$pid_file" ]; then
    pid="$(cat "$pid_file")"
    if kill "$pid" 2>/dev/null; then
      echo "[stop] 已停止 $name (pid=$pid)"
    else
      echo "[stop] $name (pid=$pid) 未在运行"
    fi
    rm -f "$pid_file"
  else
    echo "[stop] 未找到 logs/${name}.pid"
  fi
done
