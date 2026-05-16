@echo off
title BDI Face Attendance Terminal
color 0A
echo.
echo ============================================
echo   BDI Face Attendance - Python Terminal
echo   Stand in front of camera to punch IN/OUT
echo ============================================
echo.

python --version >nul 2>&1
if errorlevel 1 (
    color 0C
    echo [ERROR] Python not found. Install from python.org
    pause
    exit /b 1
)

python -c "import face_recognition" >nul 2>&1
if errorlevel 1 (
    color 0C
    echo [ERROR] face_recognition not installed.
    echo Run: pip install face_recognition
    echo.
    pause
    exit /b 1
)

cd /d "%~dp0"
python face_punch_python.py
pause
