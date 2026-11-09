@echo off
rem Double-click to restore the newest backup in deploy\backups. Asks before changing anything.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0deploy\restore-server.ps1" %*
