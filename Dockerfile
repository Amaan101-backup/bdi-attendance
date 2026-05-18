# BDI Attendance — Python Backend
# syntax=docker/dockerfile:1.4

FROM python:3.10-slim

# Build deps needed to compile dlib from source
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    cmake \
    libopenblas-dev \
    liblapack-dev \
    libx11-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# ── Parallel compilation: cuts dlib build from ~20 min → ~5 min ──
ENV MAKEFLAGS="-j4"

# ── Install dependencies FIRST (Docker/Railway caches this layer) ──
# This layer only re-runs when requirements-server.txt changes.
# All other file changes (admin.html, face_server.py etc.) skip this step.
COPY requirements-server.txt .
RUN pip install --no-cache-dir -r requirements-server.txt

# ── Copy app files (fast — no compilation needed) ──
COPY face_server.py .
COPY manifest.json .
COPY sw.js .
COPY shared.js .
COPY shared.css .
COPY attendance.html .
COPY admin.html .
COPY face_enroll.html .
COPY face_test.html .

RUN mkdir -p /app/data

EXPOSE 8080

CMD ["python", "face_server.py"]
