@echo off
title BDI — Upload Faces to Cloud
color 0A
echo.
echo  ===================================================
echo   BDI Attendance — Upload Faces to Railway Cloud
echo  ===================================================
echo.
echo  This will upload your enrolled faces to the cloud
echo  server so the website can recognize them.
echo.
cd /d "%~dp0"
python upload_encodings_to_railway.py
pause
