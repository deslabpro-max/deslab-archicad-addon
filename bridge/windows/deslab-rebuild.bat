@echo off
rem deslab: one-click rebuild of a test measured plan (bridge/README.md). Usage: deslab-rebuild.bat kv94
chcp 65001 >nul
cd /d "%~dp0deslab-bridge-src"
git pull -q
cd bridge
rem deslab: the PATH python on this PC is the Windows Store stub, use the bridge venv
set PY=python
if exist ".venv\Scripts\python.exe" set PY=.venv\Scripts\python.exe
%PY% -m deslab_bridge.rebuild %*
pause
