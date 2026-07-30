@echo off
setlocal EnableDelayedExpansion
chcp 65001 >nul

if exist "%~dp0.venv\Scripts\python.exe" (
  set "BOOTSTRAP_PY=%~dp0.venv\Scripts\python.exe"
) else (
  call "%~dp0scripts\find_python.bat"
  set "BOOTSTRAP_PY=!PY!"
)

if "%BOOTSTRAP_PY%"=="" (
  echo [X] Python not found. Install Python 3.10+ and try again.
  exit /b 1
)

if not exist "%~dp0.venv\Scripts\python.exe" (
  echo [1/3] Creating virtual environment...
  "%BOOTSTRAP_PY%" -m venv "%~dp0.venv"
  if errorlevel 1 exit /b 1
)

echo [2/3] Upgrading pip...
"%~dp0.venv\Scripts\python.exe" -m pip install --upgrade pip
if errorlevel 1 exit /b 1

echo [3/3] Installing dependencies...
"%~dp0.venv\Scripts\python.exe" -m pip install -r "%~dp0requirements.txt"
if errorlevel 1 exit /b 1

echo.
echo [OK] Environment ready: %~dp0.venv
echo Run start_all.bat or start_gui.bat next.
endlocal
