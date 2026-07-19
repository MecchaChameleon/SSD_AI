@echo off
setlocal
set "PS_EXE=%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe"
set "PS_SCRIPT=%~dp0start_ai_tunnel.ps1"
set "EXTRA_ARG="
if /I "%~1"=="--smoke-test" set "EXTRA_ARG=-SmokeTest"
"%PS_EXE%" -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%PS_SCRIPT%" %EXTRA_ARG%
if errorlevel 1 pause
endlocal
