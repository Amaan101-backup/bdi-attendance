@echo off
title BDI Face Recognition Server
color 0A
echo.
echo ============================================
echo   BDI Face Recognition Server
echo   Running at http://localhost:5000
echo.
echo   Keep this window OPEN while using
echo   the attendance system.
echo.
echo   Press Ctrl+C to stop.
echo ============================================
echo.
python face_server.py
pause
