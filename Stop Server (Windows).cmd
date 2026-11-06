@echo off
rem Double-click to stop the Fexor server. Your data is kept.
cd /d "%~dp0deploy"
docker compose --env-file .env down
echo.
echo Fexor server stopped. Your data is kept; run Start Server to start it again.
pause
