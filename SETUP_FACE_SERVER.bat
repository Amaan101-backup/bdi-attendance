@echo off
title BDI Face Recognition Server - Setup
color 0A
echo.
echo ============================================
echo   BDI Attendance - Face Recognition Setup
echo ============================================
echo.
echo Installing required Python packages...
echo This takes 2-5 minutes on first run.
echo.
pip install flask flask-cors face_recognition numpy pillow
echo.
echo ============================================
echo   Setup complete!
echo   Now run START_FACE_SERVER.bat
echo ============================================
pause
