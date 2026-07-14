@echo off
setlocal
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup-local-windows.ps1"
if errorlevel 1 (
  echo.
  echo AURORA startup failed. Review the error above.
  pause
)
endlocal
