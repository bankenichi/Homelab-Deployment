@echo off
REM Proton MCP — Windows entry point for setup.py
REM Looks for Python on PATH, then runs the cross-platform setup script.

setlocal

set "SCRIPT_DIR=%~dp0"
set "SCRIPT=%SCRIPT_DIR%setup.py"

if not exist "%SCRIPT%" (
  echo [setup] ERROR: setup.py not found next to setup.bat
  echo [setup] Expected at: %SCRIPT%
  exit /b 1
)

REM Prefer `py -3` (the Windows Python Launcher), then fall back to `python`.
where py >nul 2>&1
if %ERRORLEVEL%==0 (
  py -3 "%SCRIPT%" %*
  exit /b %ERRORLEVEL%
)

where python >nul 2>&1
if %ERRORLEVEL%==0 (
  python "%SCRIPT%" %*
  exit /b %ERRORLEVEL%
)

echo [setup] ERROR: Python 3 was not found on PATH.
echo [setup] Install it from https://www.python.org/downloads/  (or `winget install Python.Python.3.12`)
echo [setup] Then re-run setup.bat.
exit /b 1
