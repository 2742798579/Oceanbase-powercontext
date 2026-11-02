#!/usr/bin/env bash
# 一键启动（macOS / Linux）。首次运行会创建虚拟环境、装依赖、下载向量模型。
set -e
cd "$(dirname "$0")"

if [ ! -x ".venv/bin/python" ]; then
  echo "[1/4] 创建虚拟环境"
  python3 -m venv .venv
fi

echo "[2/4] 安装依赖"
.venv/bin/python -m pip install -q -r requirements.txt

echo "[3/4] 准备向量模型（已存在会自动跳过）"
.venv/bin/python scripts/fetch_model.py

echo "[4/4] 启动服务 http://127.0.0.1:8080"
.venv/bin/python run.py
