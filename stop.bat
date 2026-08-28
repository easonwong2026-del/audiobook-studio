@echo off
chcp 65001 >nul
REM Audiobook Studio stop helper -- lifecycle logic lives in launcher.py
python "%~dp0launcher.py" --stop
pause
