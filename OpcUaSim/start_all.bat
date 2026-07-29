@echo off
REM ============================================================
REM XUSE OPC UA Simulation - launch Server + Handshake Agent
REM (opens two console windows, one for each process)
REM ============================================================
setlocal
chcp 65001 > nul

echo Starting OPC UA Server (new window) ...
start "XUSE-Server" /D "%~dp0" cmd /k call start.bat %*

echo Waiting 3 seconds for server to be ready ...
timeout /t 3 /nobreak > nul

echo Starting Handshake Agent (new window) ...
start "XUSE-HandshakeAgent" /D "%~dp0" cmd /k call start_handshake.bat %*

endlocal
