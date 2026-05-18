# BDI Attendance — Python Backend
# Uses dlib-bin (pre-compiled wheel) — no C++ compilation needed, fast deploys

FROM python:3.10-slim

# Minimal runtime deps only — no build-essential or cmake needed
RUN apt-get update && apt-get install -y --no-install-recommends \
    libopenblas0 \
    libgomp1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python dependencies
# dlib-bin = pre-compiled dlib wheel (~30s) vs dlib from source (~20 min)
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

RUN mkdir -p /app/data

EXPOSE 8080

CMD ["python", "face_server.py"]
