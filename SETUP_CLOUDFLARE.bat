@echo off
title BDI - Cloudflare Tunnel Setup
color 0B
echo.
echo =====================================================
echo   BDI Attendance - Cloudflare Tunnel Setup
echo =====================================================
echo.
echo This will download cloudflared and create a
echo secure public URL for your attendance system.
echo.
echo Requirements:
echo   - Internet connection
echo   - Python server works locally first
echo =====================================================
echo.

cd /d "%~dp0"

REM Check if cloudflared already exists
if exist "cloudflared.exe" (
    echo [OK] cloudflared.exe already downloaded.
    goto :LOGIN
)

echo [1/2] Downloading cloudflared for Windows...
echo.

REM Download cloudflared using PowerShell
powershell -Command "& { $url = 'https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe'; $out = 'cloudflared.exe'; Write-Host 'Downloading...'; Invoke-WebRequest -Uri $url -OutFile $out -UseBasicParsing; Write-Host 'Done!' }"

if not exist "cloudflared.exe" (
    color 0C
    echo [ERROR] Download failed. Check internet connection.
    echo.
    echo Manual download:
    echo 1. Go to: https://github.com/cloudflare/cloudflared/releases/latest
    echo 2. Download: cloudflared-windows-amd64.exe
    echo 3. Rename to: cloudflared.exe
    echo 4. Put it in this folder: %~dp0
    pause
    exit /b 1
)

echo [OK] cloudflared.exe downloaded!
echo.

:LOGIN
echo [2/2] Login to Cloudflare...
echo.
echo Your browser will open Cloudflare login page.
echo Sign in with your FREE Cloudflare account.
echo (Create one free at cloudflare.com if you don't have one)
echo.
pause

cloudflared.exe tunnel login

echo.
echo =====================================================
echo   Login complete!
echo   Now run: START_CLOUDFLARE.bat to go live.
echo =====================================================
echo.
pause
