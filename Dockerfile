# BDI Attendance — Python Backend
# Uses pre-built dlib to avoid long compile times

FROM python:3.10-slim

# System deps for dlib + OpenCV
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    cmake \
    libopenblas-dev \
    liblapack-dev \
    libx11-dev \
    libgtk-3-dev \
    wget \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python packages
# dlib first (heaviest — cached in Docker layer)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy app files
COPY face_server.py .
COPY manifest.json .
COPY sw.js .
COPY shared.js .
COPY shared.css .
COPY attendance.html .
COPY admin.html .
COPY face_enroll.html .
COPY face_test.html .

# Create data directory for persistent storage
RUN mkdir -p /app/data

# Expose port
EXPOSE 8080

# Run
CMD ["python", "face_server.py"]
