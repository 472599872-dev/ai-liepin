@echo off
setlocal
cd /d "%~dp0"

if not exist ".venv-win\Scripts\python.exe" (
  echo Initializing Python environment...
  py -3.11 -m venv ".venv-win"
)

set "PYTHON=.venv-win\Scripts\python.exe"

echo Checking dependencies...
"%PYTHON%" -m pip install --upgrade pip setuptools wheel
"%PYTHON%" -m pip install -e .

"%PYTHON%" scripts\admin_tool_gui.py

if errorlevel 1 pause
