@echo off
rem deslab: one-click rebuild of a test measured plan (bridge/README.md). Usage: deslab-rebuild.bat kv94
chcp 65001 >nul
cd /d "%~dp0deslab-bridge-src"
git pull -q
cd bridge
python -m deslab_bridge.rebuild %1
pause
