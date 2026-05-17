#!/bin/bash
# BDI Attendance — Cloudflare Pages build script
# Copies static files into /dist for deployment

set -e
mkdir -p dist

# Copy all frontend files
cp attendance.html dist/
cp admin.html dist/
cp face_enroll.html dist/
cp face_test.html dist/
cp shared.js dist/
cp shared.css dist/
cp manifest.json dist/
cp sw.js dist/
cp _headers dist/

echo "Build complete. Files in dist/:"
ls -la dist/
