@echo off
title BDI - Update Cloud API URL
color 0B
echo.
echo =====================================================
echo   Update Backend API URL
echo =====================================================
echo.
echo After deploying to Railway, you get a URL like:
echo   https://bdi-attendance-production.up.railway.app
echo.
set /p API_URL="Paste your Railway URL here: "

if "%API_URL%"=="" (
    echo No URL entered. Exiting.
    pause & exit /b 1
)

echo.
echo Updating shared.js with: %API_URL%

REM Use PowerShell to do a proper string replace in shared.js
powershell -Command "(Get-Content 'shared.js') -replace \"const PY_SERVER = .*\", \"const PY_SERVER = '%API_URL%';  // Cloud backend\" | Set-Content 'shared.js'"

echo.
echo [OK] shared.js updated!
echo.
echo Now push to GitHub to update Cloudflare Pages:
git add shared.js
git commit -m "Set cloud backend URL to %API_URL%"
git push

echo.
echo Done! Your system now uses the cloud backend.
pause
