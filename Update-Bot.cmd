@echo off
setlocal
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0Update-Bot.ps1"
set "update_exit_code=%ERRORLEVEL%"
echo.
pause
exit /b %update_exit_code%
