@echo off
title BDI Attendance System - Web Server
color 0A
echo.
echo =====================================================
echo   BDI Attendance System
echo   Powered by Python + Flask
echo =====================================================
echo.
echo   The website will open automatically in your browser.
echo.
echo   Bookmarks to save:
echo   Main page:   http://localhost:5000
echo   Admin:       http://localhost:5000/admin
echo   Enrollment:  http://localhost:5000/enroll
echo.
echo   Keep this window OPEN while using the system.
echo   Press Ctrl+C to stop the server.
echo =====================================================
echo.

python --version >nul 2>&1
if errorlevel 1 (
    color 0C
    echo [ERROR] Python is not installed.
    echo Download from: https://www.python.org/downloads/
    pause & exit /b 1
)

python -c "import flask" >nul 2>&1
if errorlevel 1 (
    echo [!] Installing Flask...
    pip install flask flask-cors pillow numpy
    echo.
)

python -c "import face_recognition" >nul 2>&1
if errorlevel 1 (
    echo [WARN] face_recognition not installed.
    echo        System will use browser AI instead.
    echo        For best accuracy: run INSTALL_PYTHON_DEPS.bat
    echo.
)

cd /d "%~dp0"
python face_server.py
echo.
if errorlevel 1 (
    color 0C
    echo [ERROR] Server failed to start. See error above.
)
pause
