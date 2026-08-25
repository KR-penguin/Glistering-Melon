@echo off
REM Serves the landing page on http://localhost:5599/Landing.dc.html
REM
REM The backend runs on the home server (192.168.45.100:8000) and is started
REM separately -- see start-backend.txt. This only serves the static site, so
REM you must be on the same network as the server for the page to reach it.

cd /d "%~dp0"
echo.
echo   Landing page:  http://localhost:5599/Landing.dc.html
echo   Backend:       http://192.168.45.100:8000
echo   Admin:         http://192.168.45.100:8000/admin
echo   Login:         http://192.168.45.100:8000/login
echo.
echo   Ctrl+C to stop.
echo.
python -m http.server 5599
