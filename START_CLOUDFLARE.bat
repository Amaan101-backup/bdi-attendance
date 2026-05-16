@echo off
title BDI Attendance - Live on Internet
color 0A
echo.
echo =====================================================
echo   BDI Attendance System
echo   Going LIVE on the internet via Cloudflare
echo =====================================================
echo.

cd /d "%~dp0"

REM Check cloudflared exists
if not exist "cloudflared.exe" (
    color 0C
    echo [ERROR] cloudflared.exe not found.
    echo Run SETUP_CLOUDFLARE.bat first.
    pause
    exit /b 1
)

REM Check Python
python --version >nul 2>&1
if errorlevel 1 (
    color 0C
    echo [ERROR] Python not found.
    pause
    exit /b 1
)

echo Starting BDI web server in background...
start "BDI Web Server" /min cmd /c "python face_server.py"

echo Waiting for server to start...
timeout /t 3 /nobreak >nul

echo.
echo =====================================================
echo   Starting Cloudflare Tunnel...
echo   Your public URL will appear below.
echo   Share this URL with all your devices/sites.
echo =====================================================
echo.

REM Start tunnel - URL appears in output
cloudflared.exe tunnel --url http://localhost:5000

echo.
echo [INFO] Tunnel closed.
pause
