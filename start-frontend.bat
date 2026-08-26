@echo off
REM Serves the landing page on http://localhost:5599/
REM
REM This is the LOCAL PREVIEW. Production is Cloudflare Pages at
REM https://glisteringmelon.org, deployed from public/ on every push.
REM
REM Served from localhost, the page talks to the backend on the LAN
REM (192.168.45.100:8000), so you must be on the same network.

cd /d "%~dp0"
echo.
echo   Landing page:  http://localhost:5599/
echo   Backend:       http://192.168.45.100:8000
echo   Admin:         http://192.168.45.100:8000/admin
echo   Login:         http://192.168.45.100:8000/login
echo.
echo   Ctrl+C to stop.
echo.
python -m http.server 5599 --directory public
