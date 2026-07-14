@echo off
setlocal
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup-local-windows.ps1"
if errorlevel 1 (
  echo.
  echo AURORA setup failed. Review the error above.
  pause
)
endlocal
