@echo off
rem Double-click on the kiosk computer to install the Fexor kiosk app.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0deploy\kiosk\install-windows.ps1"
pause
