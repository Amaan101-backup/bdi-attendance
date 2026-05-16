@echo off
title BDI Attendance - LIVE (Permanent URL)
color 0A
echo.
echo =====================================================
echo   BDI Attendance System - PERMANENTLY LIVE
echo =====================================================
echo.

cd /d "%~dp0"

if not exist "cloudflared.exe" (
    color 0C
    echo [ERROR] cloudflared.exe not found.
    echo Run SETUP_CLOUDFLARE.bat first.
    pause & exit /b 1
)

if not exist "cloudflare-config.yml" (
    color 0E
    echo [WARN] No permanent tunnel configured.
    echo Run SETUP_PERMANENT_TUNNEL.bat first.
    echo.
    echo Starting with temporary URL instead...
    call START_CLOUDFLARE.bat
    exit /b
)

echo Starting BDI web server...
start "BDI Web Server" /min cmd /c "python face_server.py"
timeout /t 3 /nobreak >nul

echo.
echo Tunnel is live at your permanent domain.
echo All devices can now access the system.
echo.
echo Press Ctrl+C to stop.
echo.

cloudflared.exe tunnel --config cloudflare-config.yml run

pause
