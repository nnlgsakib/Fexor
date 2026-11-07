@echo off
rem Double-click to stop the Fexor server completely. Your data is kept.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0deploy\stop-server.ps1"
