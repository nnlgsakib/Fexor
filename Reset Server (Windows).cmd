@echo off
rem Double-click to DELETE the Fexor server and all its data, to start again from scratch.
rem It lists what will be deleted and asks you to type DELETE first.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0deployeset-server.ps1"
