@echo off
title BDI - Permanent Cloudflare Tunnel Setup
color 0B
echo.
echo =====================================================
echo   BDI - Permanent Tunnel Setup
echo   Your URL will NEVER change after this.
echo =====================================================
echo.
echo This creates a permanent named tunnel.
echo You need:
echo   1. Cloudflare account (free)
echo   2. A domain added to Cloudflare (can be free)
echo      OR use your existing domain: bdiuae.com
echo.

cd /d "%~dp0"

if not exist "cloudflared.exe" (
    echo [ERROR] Run SETUP_CLOUDFLARE.bat first.
    pause & exit /b 1
)

echo Step 1: Create a named tunnel called "bdi-attendance"
echo.
cloudflared.exe tunnel create bdi-attendance

echo.
echo =====================================================
echo Step 2: Enter your domain details
echo =====================================================
echo.
set /p DOMAIN="Enter your domain (e.g. attendance.bdiuae.com): "

echo.
echo Step 3: Route domain to tunnel...
cloudflared.exe tunnel route dns bdi-attendance %DOMAIN%

echo.
echo Step 4: Creating config file...

REM Get tunnel ID
for /f "tokens=*" %%i in ('cloudflared.exe tunnel list ^| findstr "bdi-attendance"') do set TUNNEL_LINE=%%i

REM Write config file
(
echo url: http://localhost:5000
echo tunnel: bdi-attendance
echo credentials-file: %USERPROFILE%\.cloudflared\bdi-attendance.json
echo.
echo ingress:
echo   - hostname: %DOMAIN%
echo     service: http://localhost:5000
echo   - service: http_status:404
) > cloudflare-config.yml

echo.
echo =====================================================
echo   SETUP COMPLETE!
echo =====================================================
echo.
echo   Your permanent URL: https://%DOMAIN%
echo.
echo   Now run: START_PERMANENT_TUNNEL.bat every day
echo   to go live at the same URL.
echo =====================================================
echo.
pause
