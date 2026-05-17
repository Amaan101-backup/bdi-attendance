# BDI Attendance — Python Backend
# Uses pre-built dlib-bin to avoid long compile times

FROM python:3.10-slim

# Minimal system deps for OpenCV + face_recognition
RUN apt-get update && apt-get install -y --no-install-recommends \
    libglib2.0-0 \
    libsm6 \
    libxext6 \
    libxrender-dev \
    libgomp1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python packages (dlib-bin = pre-built, no compilation needed)
COPY requirements-server.txt .
RUN pip install --no-cache-dir -r requirements-server.txt

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
