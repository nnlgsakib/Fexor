@echo off
rem Double-click to back up the Fexor server (database and keys) into deploy\backups.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0deploy\backup-server.ps1"
