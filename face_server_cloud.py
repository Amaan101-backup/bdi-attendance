"""
BDI Attendance — Cloud Server (No dlib required)
=================================================
Lightweight version for Railway / cloud deployment.
Face recognition runs in the browser via face-api.js.
This server handles: data storage, sync, attendance records.

No dlib, no cmake, no compilation needed.
Deploys in under 2 minutes.
"""

import os, json, base64, io, logging
from datetime import datetime
from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
from werkzeug.middleware.proxy_fix import ProxyFix

# ── Config ────────────────────────────────────────────────────────────────────
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.environ.get('DATA_DIR', '/app/data' if os.path.exists('/app') else BASE_DIR)
PORT     = int(os.environ.get('PORT', 5000))

os.makedirs(DATA_DIR, exist_ok=True)

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
log = logging.getLogger('BDI-Cloud')

# ── Flask ─────────────────────────────────────────────────────────────────────
app = Flask(__name__, static_folder=BASE_DIR, static_url_path='')
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
CORS(app, origins='*')

# ── File paths ────────────────────────────────────────────────────────────────
ENCODINGS_FILE  = os.path.join(DATA_DIR, 'face_encodings.json')
EMPLOYEES_FILE  = os.path.join(DATA_DIR, 'employees.json')
PY_RECS_FILE    = os.path.join(DATA_DIR, 'python_attendance.json')
APP_DATA_FILE   = os.path.join(DATA_DIR, 'app_data.json')

# ── In-memory state ───────────────────────────────────────────────────────────
face_db  = {}   # uid → list of 128-dim descriptor arrays (from face-api.js)
emp_db   = []
py_recs  = []
app_data = {}

# ── Helpers ───────────────────────────────────────────────────────────────────
def _load(path, default):
    if os.path.exists(path):
        try:
            with open(path) as f: return json.load(f)
        except: pass
    return default

def _save(path, data):
    try:
        with open(path, 'w') as f: json.dump(data, f)
    except Exception as e:
        log.error(f'Save failed {path}: {e}')

def load_all():
    global face_db, emp_db, py_recs, app_data
    face_db  = _load(ENCODINGS_FILE, {})
    emp_db   = _load(EMPLOYEES_FILE, [])
    py_recs  = _load(PY_RECS_FILE,  [])
    app_data = _load(APP_DATA_FILE, {})
    log.info(f'Loaded: {len(face_db)} faces, {len(emp_db)} employees, '
             f'{len(py_recs)} punches, {len(app_data.get("employees",[]))} app employees')

# ── Page routes ───────────────────────────────────────────────────────────────
@app.route('/')
@app.route('/attendance.html')
def home():       return send_from_directory(BASE_DIR, 'attendance.html')

@app.route('/admin')
@app.route('/admin.html')
def admin():      return send_from_directory(BASE_DIR, 'admin.html')

@app.route('/enroll')
@app.route('/face_enroll.html')
def enroll():     return send_from_directory(BASE_DIR, 'face_enroll.html')

@app.route('/test')
@app.route('/face_test.html')
def face_test():  return send_from_directory(BASE_DIR, 'face_test.html')

@app.route('/shared.js')
def shared_js():  return send_from_directory(BASE_DIR, 'shared.js')

@app.route('/shared.css')
def shared_css(): return send_from_directory(BASE_DIR, 'shared.css')

@app.route('/manifest.json')
def manifest():
    r = send_from_directory(BASE_DIR, 'manifest.json')
    r.headers['Content-Type'] = 'application/manifest+json'
    return r

@app.route('/sw.js')
def sw():
    r = send_from_directory(BASE_DIR, 'sw.js')
    r.headers['Content-Type'] = 'application/javascript'
    r.headers['Service-Worker-Allowed'] = '/'
    return r

# ── Status ────────────────────────────────────────────────────────────────────
@app.route('/status')
def status():
    return jsonify({
        'ok': True,
        'server': 'BDI Cloud Server',
        'version': '2.0',
        'mode': 'cloud',
        'enrolled': len(face_db),
        'employees': list(face_db.keys())
    })
