@echo off
rem 一键启动（Windows）。首次运行会创建虚拟环境、装依赖、下载向量模型。
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  echo [1/4] 创建虚拟环境
  python -m venv .venv || goto :error
)

echo [2/4] 安装依赖
".venv\Scripts\python.exe" -m pip install -q -r requirements.txt || goto :error

echo [3/4] 准备向量模型（已存在会自动跳过）
".venv\Scripts\python.exe" scripts\fetch_model.py

echo [4/4] 启动服务 http://127.0.0.1:8080
".venv\Scripts\python.exe" run.py
goto :eof

:error
echo 启动失败，请检查上面的报错。
exit /b 1
