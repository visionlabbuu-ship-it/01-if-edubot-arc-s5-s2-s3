@echo off
setlocal
set "ARC_SIM_MODE=1"
cd /d "%~dp0"
python -m s2_vision_coordination.web
