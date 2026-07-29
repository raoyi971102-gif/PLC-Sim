@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
call "%~dp0_find_python.bat"
if "%PY%"=="" (
  echo [X] Python not found. Run setup_venv.bat or install Python 3.10+.
  pause
  exit /b 1
)

set "HOST=127.0.0.1"
set "PORT=18765"

echo ========================================================================
echo  OpcUaSim GUI
echo  Python : %PY%
echo  URL    : http://%HOST%:%PORT%/
echo ========================================================================
echo.
"%PY%" -m gui.backend --host %HOST% --port %PORT%
echo.
echo Server exited. Press any key to close.
pause >nul
endlocal
