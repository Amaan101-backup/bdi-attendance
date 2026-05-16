@echo off
title BDI - Install Python Dependencies
color 0E
echo.
echo ============================================
echo   BDI Face Recognition - Python Setup
echo ============================================
echo.
echo This will install all required packages.
echo Please wait, this may take several minutes...
echo.

REM Upgrade pip first
python -m pip install --upgrade pip

echo.
echo [1/4] Installing Flask (web server)...
pip install flask flask-cors

echo.
echo [2/4] Installing image processing libraries...
pip install numpy pillow opencv-python

echo.
echo [3/4] Installing CMake (required for dlib)...
pip install cmake

echo.
echo [4/4] Installing face_recognition (dlib-based)...
echo NOTE: This step may take 5-10 minutes to compile dlib.
pip install dlib
pip install face_recognition

echo.
echo ============================================
echo   Installation Complete!
echo ============================================
echo.
echo You can now run:
echo   START_FACE_SERVER.bat  - Start the recognition server
echo   START_ENROLL.bat       - Enroll employee faces
echo.
pause
