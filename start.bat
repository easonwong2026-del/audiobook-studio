@echo off
chcp 65001 >nul
set AUDIOBOOK_STUDIO_PYTHON=C:\Users\rakliang\WorkBuddy\2026-06-28-19-01-02\index-tts\.venv\Scripts\python.exe
echo Starting Audiobook Studio ...
"%AUDIOBOOK_STUDIO_PYTHON%" "%~dp0launcher.py"
pause
