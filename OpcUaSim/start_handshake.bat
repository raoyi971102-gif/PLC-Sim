@echo off
REM ============================================================
REM XUSE Handshake Agent - launcher (independent OPC UA client)
REM
REM Prerequisite: server.py must already be running.
REM
REM Usage:
REM   start_handshake.bat                                (default url, default CSV)
REM   start_handshake.bat "D:\path\to\my.csv"            (use given CSV, must match server)
REM   start_handshake.bat --config config.yaml           (custom timing)
REM   start_handshake.bat --url opc.tcp://host:port/xuse_sim/
REM   (drag & drop CSV file(s) onto this bat also works)
REM ============================================================
setlocal EnableDelayedExpansion
chcp 65001 > nul
set PYTHONIOENCODING=utf-8
call "%~dp0_find_python.bat"
if "%PY%"=="" (
    echo [X] Python not found. Run setup_venv.bat or install Python 3.10+.
    exit /b 1
)

set "CSV_ARGS="
set "EXTRA_ARGS="

:loop
if "%~1"=="" goto after_parse
if /i "%~x1"==".csv" (
    set CSV_ARGS=!CSV_ARGS! --csv "%~1"
) else (
    set EXTRA_ARGS=!EXTRA_ARGS! %1
)
shift
goto loop

:after_parse
echo.
echo ==============================================================
echo   XUSE Handshake Agent (OPC UA client)
echo   Target : opc.tcp://127.0.0.1:4855/xuse_sim/ (override with --url)
if not "%CSV_ARGS%"=="" (
    echo   CSV    : %CSV_ARGS%
) else (
    echo   CSV    : ^(default^) data\demo_variables.csv
)
if not "%EXTRA_ARGS%"=="" echo   Extra  : %EXTRA_ARGS%
echo   Ctrl+C to stop
echo ==============================================================
echo.

"%PY%" "%~dp0handshake_agent.py" %CSV_ARGS% %EXTRA_ARGS%

endlocal
