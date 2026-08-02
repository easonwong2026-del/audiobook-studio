@echo off
chcp 65001 >nul
REM Stop only the Audiobook Studio service instance recorded by launcher.py
python "%~dp0launcher.py" --stop
pause
