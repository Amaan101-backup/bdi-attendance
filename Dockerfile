# BDI Attendance — Python Backend
# Uses dlib-bin (pre-compiled wheel) — no C++ compilation needed, fast deploys

FROM python:3.10-slim

# Minimal runtime deps only — no build-essential or cmake needed
RUN apt-get update && apt-get install -y --no-install-recommends \
    libopenblas0 \
    libgomp1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Step 1: Install dlib-bin FIRST (pre-compiled wheel, satisfies the dlib requirement)
# Step 2: Install face_recognition with --no-deps so pip doesn't try to pull/build dlib from source
# Step 3: Install remaining packages
RUN pip install --no-cache-dir dlib-bin==19.24.1
RUN pip install --no-cache-dir face_recognition==1.3.0 --no-deps
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
