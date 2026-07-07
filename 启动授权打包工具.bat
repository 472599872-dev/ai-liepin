@echo off
setlocal
cd /d "%~dp0"

if not exist ".venv-win\Scripts\python.exe" (
  echo 正在初始化 Python 环境...
  py -3.11 -m venv ".venv-win"
)

set "PYTHON=.venv-win\Scripts\python.exe"

echo 正在检查依赖...
"%PYTHON%" -m pip install --upgrade pip setuptools wheel
"%PYTHON%" -m pip install -e .

"%PYTHON%" scripts\admin_tool_gui.py

if errorlevel 1 pause
