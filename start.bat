@echo off
chcp 65001 >nul
REM Audiobook Studio launcher (no personal absolute paths; repo is relocatable)
set "PY="
if defined AUDIOBOOK_STUDIO_PYTHON set "PY=%AUDIOBOOK_STUDIO_PYTHON%"
if not defined PY (
  if exist "%~dp0..\index-tts\.venv\Scripts\python.exe" (
    set "PY=%~dp0..\index-tts\.venv\Scripts\python.exe"
  )
)
if not defined PY set "PY=python"
echo Starting Audiobook Studio ...
"%PY%" "%~dp0launcher.py"
pause
