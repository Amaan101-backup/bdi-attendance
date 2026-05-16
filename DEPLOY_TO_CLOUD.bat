@echo off
title BDI - Deploy to Cloud
color 0B
echo.
echo =====================================================
echo   BDI Attendance - Cloud Deployment Helper
echo =====================================================
echo.
echo This will push your code to GitHub.
echo Then you connect GitHub to Railway + Cloudflare Pages.
echo.

cd /d "%~dp0"

REM Check git
git --version >nul 2>&1
if errorlevel 1 (
    color 0C
    echo [ERROR] Git not installed.
    echo Download from: https://git-scm.com/download/win
    pause & exit /b 1
)

REM Check if git repo exists
if not exist ".git" (
    echo [GIT] Initializing repository...
    git init
    echo.
)

REM Add all files
echo [GIT] Adding files...
git add .
git status

echo.
set /p MSG="Enter commit message (or press ENTER for 'Update BDI system'): "
if "%MSG%"=="" set MSG=Update BDI system

git commit -m "%MSG%"

echo.
echo =====================================================
echo  Do you have a GitHub repo set up?
echo =====================================================
echo.
echo  If NO: 
echo    1. Go to github.com
echo    2. Click "New repository"
echo    3. Name it: bdi-attendance
echo    4. Copy the repo URL (e.g. https://github.com/yourname/bdi-attendance.git)
echo.
set /p REPO="Paste your GitHub repo URL here: "

if not "%REPO%"=="" (
    git remote remove origin 2>nul
    git remote add origin %REPO%
    git branch -M main
    git push -u origin main
    echo.
    echo [OK] Code pushed to GitHub!
) else (
    echo [SKIP] No repo URL entered. Push manually later.
)

echo.
echo =====================================================
echo   NEXT STEPS:
echo =====================================================
echo.
echo   STEP 1 - Deploy Python backend (Railway.app):
echo   1. Go to: https://railway.app
echo   2. Sign up free with GitHub
echo   3. Click "New Project"
echo   4. Select "Deploy from GitHub repo"
echo   5. Pick "bdi-attendance"
echo   6. Railway auto-detects Dockerfile and deploys
echo   7. Copy your Railway URL (e.g. bdi-xxx.railway.app)
echo.
echo   STEP 2 - Update your frontend API URL:
echo   (run UPDATE_API_URL.bat after you have Railway URL)
echo.
echo   STEP 3 - Deploy frontend (Cloudflare Pages):
echo   1. Go to: https://pages.cloudflare.com
echo   2. Click "Create a project"
echo   3. Connect GitHub repo "bdi-attendance"
echo   4. Build settings: leave blank (static site)
echo   5. Deploy!
echo   6. Add custom domain: attendance.bdiuae.com
echo.
pause
