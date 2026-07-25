@echo off
chcp 65001 >nul
REM Audiobook Studio launcher -- delegates interpreter detection to launcher.py
python "%~dp0launcher.py"
pause
