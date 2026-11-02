#!/usr/bin/env bash
# 一键启动后端（Linux / macOS / Git Bash）
set -euo pipefail

cd "$(dirname "$0")/../backend"

if [ ! -d .venv ]; then
  echo "[start] 创建虚拟环境"
  python3 -m venv .venv
  ./.venv/bin/pip install -q -U pip
  ./.venv/bin/pip install -q -r requirements.txt
fi

if [ ! -f .env ]; then
  echo "[start] 未找到 .env，从 .env.example 复制（默认离线哈希嵌入）"
  cp .env.example .env
fi

# 让 .env 里的变量生效
set -a
# shellcheck disable=SC1091
. ./.env
set +a

if [ ! -d "${SEEKDB_PATH:-./data/seekdb}" ]; then
  echo "[start] 初始化 seekdb 与预置旧账"
  ./.venv/bin/python seed.py
fi

PORT="${PORT:-8077}"
echo "[start] 后端启动于 http://0.0.0.0:${PORT}"
exec ./.venv/bin/python -m uvicorn app:app --host 0.0.0.0 --port "${PORT}"
