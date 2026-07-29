@echo off
REM _find_python.bat -- Detect a real python.exe (skip WindowsApps stub).
REM  Priority: project .venv > %PYTHON% > known Miniforge paths > PATH
set "PY="
if exist "%~dp0.venv\Scripts\python.exe" set "PY=%~dp0.venv\Scripts\python.exe"
if defined PYTHON (
  if "%PY%"=="" if exist "%PYTHON%" set "PY=%PYTHON%"
)
if "%PY%"=="" if exist "D:\miniforge3\envs\unilab\python.exe" set "PY=D:\miniforge3\envs\unilab\python.exe"
if "%PY%"=="" if exist "D:\miniforge3\python.exe" set "PY=D:\miniforge3\python.exe"
if "%PY%"=="" (
  for /f "delims=" %%p in ('where.exe python 2^>nul ^| findstr /V /I "WindowsApps"') do (
    if not defined PY set "PY=%%p"
  )
)
