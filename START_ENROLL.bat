@echo off
title BDI Face Enrollment Tool
color 0B
echo.
echo ============================================
echo   BDI Face Enrollment Tool
echo   Digital Face Mapping + Encoding
echo ============================================
echo.

REM Check Python
python --version >nul 2>&1
if errorlevel 1 (
    color 0C
    echo [ERROR] Python is not installed or not in PATH.
    echo.
    echo Please install Python 3.8+ from:
    echo   https://www.python.org/downloads/
    echo.
    echo Make sure to check "Add Python to PATH" during install!
    echo.
    pause
    exit /b 1
)

echo [OK] Python found:
python --version
echo.

REM Check OpenCV
python -c "import cv2; print('[OK] OpenCV', cv2.__version__)" 2>nul
if errorlevel 1 (
    echo [!] OpenCV not found. Installing now...
    pip install opencv-python
    echo.
)

REM Check face_recognition (optional but recommended)
python -c "import face_recognition; print('[OK] face_recognition (dlib) found')" 2>nul
if errorlevel 1 (
    echo [!] face_recognition not found.
    echo     The tool will still work using OpenCV-only mode.
    echo     For best accuracy run: pip install face_recognition
    echo.
)

echo.
echo Starting camera enrollment tool...
echo.
echo CONTROLS:
echo   SPACE  = Capture face sample
echo   N      = Next employee
echo   ESC    = Quit
echo.
echo ============================================
echo.
cd /d "%~dp0"
python face_enroll_python.py
echo.
if errorlevel 1 (
    color 0C
    echo [ERROR] The enrollment tool exited with an error.
    echo Check the messages above for details.
)
pause
