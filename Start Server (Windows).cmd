@echo off
rem Double-click to start the Fexor server (needs Docker Desktop).
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0deploy\start-server.ps1"
