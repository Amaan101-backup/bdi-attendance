"""
BDI Attendance — Web Application Server
=========================================
Serves the full BDI Attendance website AND handles face recognition.
Access the system at: http://localhost:5000

SETUP (run once):
  pip install flask flask-cors face_recognition numpy pillow

RUN:
  python face_server.py
  Then open browser to http://localhost:5000
"""

from flask import Flask, request, jsonify, send_from_directory, redirect
from flask_cors import CORS
import numpy as np
import base64
import json
import os
import io
import webbrowser
import threading
from PIL import Image
import logging

# Import face_recognition safely
try:
    import face_recognition
    FACE_RECOGNITION_AVAILABLE = True
except ImportError as e:
    logging.warning(f'face_recognition not available: {e}')
    FACE_RECOGNITION_AVAILABLE = False

# Serve static files from same directory as this script
BASE_DIR  = os.path.dirname(os.path.abspath(__file__))

# Data directory — use /app/data on cloud (Railway mounts persistent volume there)
# Falls back to same directory when running locally
DATA_DIR  = os.environ.get('DATA_DIR', BASE_DIR)
PORT      = int(os.environ.get('PORT', 5000))

os.makedirs(DATA_DIR, exist_ok=True)

app = Flask(__name__, static_folder=BASE_DIR, static_url_path='')
CORS(app, origins='*')

# Trust Cloudflare proxy headers (so camera HTTPS detection works)
from werkzeug.middleware.proxy_fix import ProxyFix
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
log = logging.getLogger('BDI-Server')

# ── Page routes (clean URLs + .html aliases) ──────────────────────────────────
@app.route('/')
@app.route('/attendance.html')
def home():
    return send_from_directory(BASE_DIR, 'attendance.html')

@app.route('/admin')
@app.route('/admin.html')
def admin_page():
    return send_from_directory(BASE_DIR, 'admin.html')

@app.route('/enroll')
@app.route('/face_enroll.html')
def enroll_page():
    return send_from_directory(BASE_DIR, 'face_enroll.html')

@app.route('/test')
@app.route('/face_test.html')
def face_test():
    return send_from_directory(BASE_DIR, 'face_test.html')

@app.route('/shared.js')
def shared_js():
    return send_from_directory(BASE_DIR, 'shared.js')

@app.route('/shared.css')
def shared_css():
    return send_from_directory(BASE_DIR, 'shared.css')

@app.route('/manifest.json')
def manifest():
    resp = send_from_directory(BASE_DIR, 'manifest.json')
    resp.headers['Content-Type'] = 'application/manifest+json'
    return resp

@app.route('/sw.js')
def service_worker():
    resp = send_from_directory(BASE_DIR, 'sw.js')
    resp.headers['Content-Type'] = 'application/javascript'
    resp.headers['Service-Worker-Allowed'] = '/'
    return resp

# ── Storage ─────────────────────────────────────────────────────────────────
ENCODINGS_FILE = os.path.join(DATA_DIR, 'face_encodings.json')
face_db = {}   # uid → list of 128-dim encodings (numpy arrays)

def load_db():
    global face_db
    if os.path.exists(ENCODINGS_FILE):
        try:
            with open(ENCODINGS_FILE, 'r') as f:
                raw = json.load(f)
            face_db = {uid: [np.array(enc) for enc in encs] for uid, encs in raw.items()}
            log.info(f'Loaded {len(face_db)} enrolled employee(s) from disk')
        except Exception as e:
            log.error(f'Failed to load encodings: {e}')
            face_db = {}

def save_db():
    try:
        raw = {uid: [enc.tolist() for enc in encs] for uid, encs in face_db.items()}
        with open(ENCODINGS_FILE, 'w') as f:
            json.dump(raw, f)
    except Exception as e:
        log.error(f'Failed to save encodings: {e}')

def b64_to_image(b64_string):
    """Convert base64 image string to numpy RGB array."""
    if ',' in b64_string:
        b64_string = b64_string.split(',', 1)[1]
    img_bytes = base64.b64decode(b64_string)
    img = Image.open(io.BytesIO(img_bytes)).convert('RGB')
    return np.array(img)

# ── Routes ───────────────────────────────────────────────────────────────────

@app.route('/status', methods=['GET'])
def status():
    """Health check — browser pings this to see if server is running."""
    return jsonify({
        'ok': True,
        'server': 'BDI Face Recognition Server',
        'version': '1.0',
        'enrolled': len(face_db),
        'employees': list(face_db.keys()),
        'face_recognition': FACE_RECOGNITION_AVAILABLE
    })


@app.route('/enroll', methods=['POST'])
def enroll():
    """
    Enroll an employee face.
    Body: { uid, name, image_b64 }
    Adds encoding to face_db (keeps up to 5 samples per person).
    """
    data = request.json
    uid  = data.get('uid')
    name = data.get('name', uid)
    img_b64 = data.get('image_b64')

    if not uid or not img_b64:
        return jsonify({'ok': False, 'error': 'Missing uid or image_b64'}), 400

    if not FACE_RECOGNITION_AVAILABLE:
        return jsonify({'ok': False, 'error': 'Face recognition library not available on server'}), 503

    try:
        img = b64_to_image(img_b64)
        # Upsample once for small/distant faces
        encodings = face_recognition.face_encodings(img, num_jitters=2, model='large')
        if not encodings:
            return jsonify({'ok': False, 'error': 'No face detected in image — ensure face is clearly visible'})

        enc = encodings[0]
        if uid not in face_db:
            face_db[uid] = []
        face_db[uid].append(enc)
        # Keep max 5 samples per person
        if len(face_db[uid]) > 5:
            face_db[uid] = face_db[uid][-5:]

        save_db()
        log.info(f'Enrolled {name} ({uid}) — {len(face_db[uid])} sample(s) total')
        return jsonify({'ok': True, 'uid': uid, 'samples': len(face_db[uid])})

    except Exception as e:
        log.error(f'Enroll error: {e}')
        return jsonify({'ok': False, 'error': str(e)}), 500


@app.route('/recognize', methods=['POST'])
def recognize():
    """
    Identify a face from image.
    Body: { image_b64, threshold (optional, default 0.5), site_uids (optional list) }
    Returns best match uid + confidence, or unknown.
    """
    data      = request.json
    img_b64   = data.get('image_b64')
    threshold = float(data.get('threshold', 0.50))   # lower = stricter
    site_uids = data.get('site_uids')                # optional filter by site employees

    if not img_b64:
        return jsonify({'ok': False, 'error': 'Missing image_b64'}), 400

    if not FACE_RECOGNITION_AVAILABLE:
        return jsonify({'ok': False, 'error': 'Face recognition library not available on server'}), 503

    if not face_db:
        return jsonify({'ok': True, 'match': None, 'reason': 'No enrolled employees'})

    try:
        img = b64_to_image(img_b64)

        # Detect face locations first (more control)
        locations = face_recognition.face_locations(img, model='hog')
        if not locations:
            return jsonify({'ok': True, 'match': None, 'reason': 'No face detected'})

        encodings = face_recognition.face_encodings(img, locations, num_jitters=1, model='large')
        if not encodings:
            return jsonify({'ok': True, 'match': None, 'reason': 'No face encoding'})

        unknown_enc = encodings[0]

        # Filter pool by site if requested
        pool = {uid: encs for uid, encs in face_db.items()
                if site_uids is None or uid in site_uids}

        if not pool:
            return jsonify({'ok': True, 'match': None, 'reason': 'No enrolled employees for this site'})

        best_uid  = None
        best_dist = float('inf')

        for uid, stored_encs in pool.items():
            distances = face_recognition.face_distance(stored_encs, unknown_enc)
            min_dist  = float(np.min(distances))
            if min_dist < best_dist:
                best_dist = min_dist
                best_uid  = uid

        if best_dist <= threshold:
            confidence = round((1 - best_dist) * 100, 1)
            log.info(f'Recognised: {best_uid} — dist={best_dist:.3f} conf={confidence}%')
            return jsonify({'ok': True, 'match': best_uid, 'distance': best_dist, 'confidence': confidence})
        else:
            confidence = round((1 - best_dist) * 100, 1)
            log.info(f'Unknown face — best dist={best_dist:.3f} (threshold={threshold})')
            return jsonify({'ok': True, 'match': None, 'distance': best_dist, 'confidence': confidence, 'reason': 'Below threshold'})

    except Exception as e:
        log.error(f'Recognize error: {e}')
        return jsonify({'ok': False, 'error': str(e)}), 500


@app.route('/verify', methods=['POST'])
def verify():
    """
    1:1 verify — does this image match a specific enrolled employee?
    Body: { uid, image_b64, threshold (optional, default 0.5) }
    """
    data      = request.json
    uid       = data.get('uid')
    img_b64   = data.get('image_b64')
    threshold = float(data.get('threshold', 0.50))

    if not uid or not img_b64:
        return jsonify({'ok': False, 'error': 'Missing uid or image_b64'}), 400

    if uid not in face_db:
        return jsonify({'ok': True, 'match': False, 'reason': 'Employee not enrolled'})

    try:
        img = b64_to_image(img_b64)
        encodings = face_recognition.face_encodings(img, num_jitters=1, model='large')
        if not encodings:
            return jsonify({'ok': True, 'match': False, 'reason': 'No face detected'})

        distances  = face_recognition.face_distance(face_db[uid], encodings[0])
        best_dist  = float(np.min(distances))
        matched    = best_dist <= threshold
        confidence = round((1 - best_dist) * 100, 1)
        log.info(f'Verify {uid}: {"MATCH" if matched else "FAIL"} dist={best_dist:.3f} conf={confidence}%')
        return jsonify({'ok': True, 'match': matched, 'distance': best_dist, 'confidence': confidence})

    except Exception as e:
        log.error(f'Verify error: {e}')
        return jsonify({'ok': False, 'error': str(e)}), 500


@app.route('/import-encodings', methods=['POST'])
def import_encodings():
    """
    Bulk-import face encodings from face_encodings.json format.
    Body: { encodings: { uid: [[...128 floats...], ...], ... }, secret: 'bdi-import' }
    Used to upload local Python encodings to the cloud server.
    """
    data = request.json
    if data.get('secret') != 'bdi-import-2024':
        return jsonify({'ok': False, 'error': 'Unauthorized'}), 403

    incoming = data.get('encodings', {})
    if not incoming:
        return jsonify({'ok': False, 'error': 'No encodings provided'}), 400

    count_new = 0
    for uid, encs in incoming.items():
        numpy_encs = [np.array(e) for e in encs]
        if uid not in face_db:
            face_db[uid] = numpy_encs
            count_new += 1
        else:
            # Merge — keep existing + add new ones, cap at 5
            face_db[uid] = (face_db[uid] + numpy_encs)[-5:]

    save_db()
    log.info(f'Imported encodings: {len(incoming)} employees ({count_new} new)')
    return jsonify({'ok': True, 'imported': len(incoming), 'new': count_new, 'total': len(face_db)})


@app.route('/export-encodings', methods=['GET'])
def export_encodings():
    """Export all face encodings as JSON (for backup/migration)."""
    secret = request.args.get('secret', '')
    if secret != 'bdi-import-2024':
        return jsonify({'ok': False, 'error': 'Unauthorized'}), 403
    raw = {uid: [enc.tolist() for enc in encs] for uid, encs in face_db.items()}
    return jsonify({'ok': True, 'encodings': raw, 'count': len(raw)})


@app.route('/delete', methods=['POST'])
def delete():
    """Remove a person's encodings from the database."""
    uid = request.json.get('uid')
    if uid in face_db:
        del face_db[uid]
        save_db()
        log.info(f'Deleted encodings for {uid}')
        return jsonify({'ok': True})
    return jsonify({'ok': False, 'error': 'UID not found'})


@app.route('/list', methods=['GET'])
def list_enrolled():
    return jsonify({'ok': True, 'enrolled': {uid: len(encs) for uid, encs in face_db.items()}})


# ── Employee sync (HTML → Python) ────────────────────────────────────────────
EMPLOYEES_FILE = os.path.join(DATA_DIR, 'employees.json')
emp_db = []   # list of employee dicts from HTML system

def load_employees():
    global emp_db
    if os.path.exists(EMPLOYEES_FILE):
        try:
            with open(EMPLOYEES_FILE, 'r') as f:
                emp_db = json.load(f)
            log.info(f'Loaded {len(emp_db)} employee profiles from employees.json')
        except Exception as e:
            log.error(f'Failed to load employees.json: {e}')

def save_employees():
    try:
        with open(EMPLOYEES_FILE, 'w') as f:
            json.dump(emp_db, f, indent=2)
    except Exception as e:
        log.error(f'Failed to save employees.json: {e}')

@app.route('/employees', methods=['GET'])
def get_employees():
    load_employees()
    return jsonify({'ok': True, 'employees': emp_db, 'count': len(emp_db)})

@app.route('/employees', methods=['POST'])
def set_employees():
    global emp_db
    data = request.json
    employees = data.get('employees', [])
    emp_db = employees
    save_employees()
    log.info(f'Employee list updated: {len(emp_db)} employees synced from HTML')
    return jsonify({'ok': True, 'count': len(emp_db)})


# ── Python Attendance Records ─────────────────────────────────────────────────
PY_RECS_FILE = os.path.join(DATA_DIR, 'python_attendance.json')
py_recs = []   # list of attendance record dicts

def load_py_recs():
    global py_recs
    if os.path.exists(PY_RECS_FILE):
        try:
            with open(PY_RECS_FILE, 'r') as f:
                py_recs = json.load(f)
            log.info(f'Loaded {len(py_recs)} python attendance record(s)')
        except Exception as e:
            log.error(f'Failed to load python_attendance.json: {e}')
            py_recs = []

def save_py_recs():
    try:
        with open(PY_RECS_FILE, 'w') as f:
            json.dump(py_recs, f, indent=2)
    except Exception as e:
        log.error(f'Failed to save python_attendance.json: {e}')

@app.route('/punch', methods=['POST'])
def punch():
    """
    Called by face_punch_python.py when a face is recognised.
    Body: { uid, name, type ('in'/'out'), confidence, timestamp (ISO) }
    """
    global py_recs
    data       = request.json
    uid        = data.get('uid')
    name       = data.get('name', uid)
    ptype      = data.get('type', 'in').lower()         # 'in' or 'out'
    confidence = float(data.get('confidence', 0))
    timestamp  = data.get('timestamp')                   # ISO string

    if not uid:
        return jsonify({'ok': False, 'error': 'Missing uid'}), 400

    # Find employee profile from employees.json
    emp = next((e for e in emp_db if e['uid'] == uid), None)

    rec = {
        'empUid':       uid,
        'empName':      emp['name']  if emp else name,
        'empId':        emp.get('id', uid) if emp else uid,
        'dept':         emp.get('dept','')  if emp else '',
        'siteId':       emp.get('site','').split(',')[0] if emp else '',
        'siteName':     '',
        'siteCode':     'PYTHON-CAM',
        'type':         ptype,
        'time':         timestamp,
        'confidence':   confidence,
        'method':       'python-face',
        'markedBy':     'python',
        'markedByName': 'Python Camera',
        'markedByRole': 'auto',
        'timeStatus':   None,
        'source':       'python'
    }

    py_recs.append(rec)
    save_py_recs()
    log.info(f'Punch recorded: {name} ({uid}) → {ptype.upper()}  conf={confidence:.1f}%')
    return jsonify({'ok': True, 'record': rec})

@app.route('/records', methods=['GET'])
def get_records():
    """Return all Python attendance records (HTML fetches these to merge into its reports)."""
    return jsonify({'ok': True, 'records': py_recs, 'count': len(py_recs)})

@app.route('/records/clear', methods=['POST'])
def clear_records():
    global py_recs
    py_recs = []
    save_py_recs()
    return jsonify({'ok': True})


# ── Central App Data Sync (for multi-device support) ─────────────────────────
# Stores the full app state (employees, sites, supervisors, records)
# so all devices share the same data instead of using localStorage separately.
APP_DATA_FILE = os.path.join(DATA_DIR, 'app_data.json')
app_data = {}

def load_app_data():
    global app_data
    if os.path.exists(APP_DATA_FILE):
        try:
            with open(APP_DATA_FILE, 'r') as f:
                app_data = json.load(f)
            log.info(f'Loaded central app data ({len(app_data.get("employees",[]))} employees, '
                     f'{len(app_data.get("records",[]))} records)')
        except Exception as e:
            log.error(f'Failed to load app_data.json: {e}')
            app_data = {}

def save_app_data():
    try:
        with open(APP_DATA_FILE, 'w') as f:
            json.dump(app_data, f)
    except Exception as e:
        log.error(f'Failed to save app_data.json: {e}')

@app.route('/data', methods=['GET'])
def get_app_data():
    """Device fetches full app state on load."""
    # Merge Python attendance records into the shared records
    merged = list(app_data.get('records', []))
    existing_times = {r.get('time') for r in merged}
    for pr in py_recs:
        if pr.get('time') not in existing_times:
            merged.append(pr)
    return jsonify({
        'ok': True,
        'employees':   app_data.get('employees', []),
        'sites':       app_data.get('sites', []),
        'supervisors': app_data.get('supervisors', []),
        'records':     merged,
        'settings':    app_data.get('settings', {}),
        'ec':          app_data.get('ec', 100),
        'sc':          app_data.get('sc', 20),
        'version':     app_data.get('version', 1)
    })

@app.route('/data', methods=['POST'])
def set_app_data():
    """Device pushes updated app state to server (on any save)."""
    global app_data
    data = request.json
    app_data = data
    app_data['version'] = app_data.get('version', 1) + 1
    save_app_data()
    log.info(f'App data saved — {len(data.get("employees",[]))} employees, '
             f'{len(data.get("records",[]))} records (v{app_data["version"]})')
    return jsonify({'ok': True, 'version': app_data['version']})

@app.route('/data/version', methods=['GET'])
def get_data_version():
    """Lightweight check — devices poll this to detect changes from other devices."""
    return jsonify({'ok': True, 'version': app_data.get('version', 1)})


# ── Boot ─────────────────────────────────────────────────────────────────────
load_db()
load_employees()
load_py_recs()
load_app_data()

if __name__ == '__main__':
    print("\n" + "="*60)
    print("  BDI Attendance System — Web Server")
    print("="*60)
    print(f"  Website:   http://localhost:5000")
    print(f"  Admin:     http://localhost:5000/admin")
    print(f"  Enroll:    http://localhost:5000/enroll")
    print(f"  Enrolled:  {len(face_db)} employee(s)")
    print("="*60)
    print("  Opening browser automatically...")
    print("  Press Ctrl+C to stop the server.\n")

    is_cloud = os.environ.get('RAILWAY_ENVIRONMENT') or os.environ.get('RENDER')
    if not is_cloud:
        # Auto-open browser only when running locally
        def _open_browser():
            import time; time.sleep(1.5)
            webbrowser.open(f'http://localhost:{PORT}')
        threading.Thread(target=_open_browser, daemon=True).start()

    app.run(host='0.0.0.0', port=PORT, debug=False, use_reloader=False)
