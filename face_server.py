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
from PIL import Image, ExifTags
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

# Data directory — /app/data by default so Railway volume mount persists JSON files
DATA_DIR  = os.environ.get('DATA_DIR', os.path.join(BASE_DIR, 'data'))
PORT      = int(os.environ.get('PORT', 5000))

os.makedirs(DATA_DIR, exist_ok=True)

app = Flask(__name__, static_folder=BASE_DIR, static_url_path='')
CORS(app, origins='*', supports_credentials=False,
     allow_headers=['Content-Type', 'Authorization'],
     methods=['GET', 'POST', 'PUT', 'DELETE', 'OPTIONS'])

@app.after_request
def add_cors_headers(response):
    response.headers['Access-Control-Allow-Origin'] = '*'
    response.headers['Access-Control-Allow-Headers'] = 'Content-Type, Authorization'
    response.headers['Access-Control-Allow-Methods'] = 'GET, POST, PUT, DELETE, OPTIONS'
    return response

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

@app.route('/attendance-bridge.js')
def attendance_bridge():
    resp = send_from_directory(BASE_DIR, 'attendance-bridge.js')
    resp.headers['Content-Type'] = 'application/javascript'
    resp.headers['Access-Control-Allow-Origin'] = '*'
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
    """Convert base64 image string to numpy RGB array.
    Automatically corrects EXIF rotation (phone cameras often rotate images)."""
    if ',' in b64_string:
        b64_string = b64_string.split(',', 1)[1]
    img_bytes = base64.b64decode(b64_string)
    img = Image.open(io.BytesIO(img_bytes))

    # Apply EXIF rotation so face detector sees upright image
    try:
        exif = img._getexif()
        if exif:
            for tag, value in exif.items():
                if ExifTags.TAGS.get(tag) == 'Orientation':
                    if value == 3:
                        img = img.rotate(180, expand=True)
                    elif value == 6:
                        img = img.rotate(270, expand=True)
                    elif value == 8:
                        img = img.rotate(90, expand=True)
                    break
    except Exception:
        pass

    return np.array(img.convert('RGB'))

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

        # Detect face locations first (try progressively more upsampling)
        locations = face_recognition.face_locations(img, number_of_times_to_upsample=1, model='hog')
        if not locations:
            locations = face_recognition.face_locations(img, number_of_times_to_upsample=2, model='hog')
        if not locations:
            return jsonify({'ok': False, 'error': 'No face detected — move closer, ensure good lighting, face the camera directly'})

        encodings = face_recognition.face_encodings(img, known_face_locations=locations, num_jitters=2, model='large')
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
    Body: { image_b64, threshold (optional, default 0.62), site_uids (optional list of site IDs) }
    Returns: { ok, uid, name, confidence } on match, or { ok, error } on no match.
    """
    data      = request.json
    img_b64   = data.get('image_b64')
    threshold = float(data.get('threshold', 0.45))  # 0.45 = strict, requires ~55% confidence
    site_uids = data.get('site_uids')               # list of site IDs to filter employees

    if not img_b64:
        return jsonify({'ok': False, 'error': 'Missing image_b64'}), 400
    if not FACE_RECOGNITION_AVAILABLE:
        return jsonify({'ok': False, 'error': 'Face recognition not available on server'}), 503
    if not face_db:
        return jsonify({'ok': False, 'error': 'No enrolled employees. Enroll faces first.'})

    try:
        img = b64_to_image(img_b64)

        # Detect face locations
        locations = face_recognition.face_locations(img, model='hog')
        if not locations:
            # Try with upsampling for small/distant faces
            locations = face_recognition.face_locations(img, number_of_times_to_upsample=2, model='hog')
        if not locations:
            return jsonify({'ok': False, 'error': 'no_face'})

        encodings = face_recognition.face_encodings(img, locations, num_jitters=1, model='large')
        if not encodings:
            return jsonify({'ok': False, 'error': 'no_face'})

        unknown_enc = encodings[0]

        # Build employee uid → name lookup from emp_db
        emp_name_map = {}
        emp_site_map = {}  # uid → list of site ids
        for emp in emp_db:
            uid  = emp.get('uid') or emp.get('id', '')
            name = emp.get('name', uid)
            site = emp.get('site', '')
            emp_name_map[uid] = name
            emp_site_map[uid] = [s.strip() for s in site.split(',') if s.strip()] if site else []

        # Build recognition pool — filter by site if requested
        if site_uids:
            # Include employee if ANY of their assigned sites matches a requested site
            pool = {uid: encs for uid, encs in face_db.items()
                    if not emp_site_map.get(uid) or   # no site restriction = include always
                    any(s in site_uids for s in emp_site_map.get(uid, []))}
        else:
            pool = dict(face_db)  # no filter — search all enrolled employees

        if not pool:
            pool = dict(face_db)  # fallback: search everyone if site filter gave empty pool

        # Find best match
        best_uid  = None
        best_dist = float('inf')
        for uid, stored_encs in pool.items():
            distances = face_recognition.face_distance(stored_encs, unknown_enc)
            min_dist  = float(np.min(distances))
            if min_dist < best_dist:
                best_dist = min_dist
                best_uid  = uid

        confidence = round((1 - best_dist) * 100, 1)

        if best_dist <= threshold:
            name = emp_name_map.get(best_uid, best_uid)
            log.info(f'Recognised: {name} ({best_uid}) dist={best_dist:.3f} conf={confidence}%')
            return jsonify({
                'ok':         True,
                'uid':        best_uid,
                'name':       name,
                'confidence': confidence,
                'distance':   round(best_dist, 3),
            })
        else:
            log.info(f'Unknown face — best dist={best_dist:.3f} threshold={threshold}')
            return jsonify({
                'ok':    False,
                'error': f'Face not recognized (confidence {confidence}%). Move closer or re-enroll.',
                'confidence': confidence,
            })

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


@app.route('/rename-face', methods=['POST'])
def rename_face():
    """Re-key a face encoding from old_uid to new_uid (used to link faces to employees)."""
    old_uid = request.json.get('old_uid', '').strip()
    new_uid = request.json.get('new_uid', '').strip()
    if not old_uid or not new_uid:
        return jsonify({'ok': False, 'error': 'old_uid and new_uid required'})
    if old_uid not in face_db:
        return jsonify({'ok': False, 'error': f'UID {old_uid} not found on server'})
    if old_uid == new_uid:
        return jsonify({'ok': True, 'message': 'Same UID, nothing changed'})
    # Merge encodings if new_uid already exists, otherwise move
    if new_uid in face_db:
        face_db[new_uid] = (face_db[new_uid] + face_db[old_uid])[-5:]  # keep max 5 samples
    else:
        face_db[new_uid] = face_db[old_uid]
    del face_db[old_uid]
    save_db()
    log.info(f'Renamed face UID {old_uid} → {new_uid}')
    return jsonify({'ok': True, 'old_uid': old_uid, 'new_uid': new_uid})


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
    global emp_db
    load_employees()
    # Fallback: if employees.json is empty, pull from app_data.json
    if not emp_db:
        fallback = app_data.get('employees', [])
        if fallback:
            log.info(f'employees.json empty — using {len(fallback)} from app_data fallback')
            emp_db = fallback
            save_employees()   # persist so next call is instant
    return jsonify({'ok': True, 'employees': emp_db, 'count': len(emp_db)})

@app.route('/employees', methods=['POST'])
def set_employees():
    global emp_db
    data = request.json
    employees = data.get('employees', [])
    emp_db = employees
    save_employees()
    # Also keep app_data in sync so fallback always has fresh data
    app_data['employees'] = employees
    log.info(f'Employee list updated: {len(emp_db)} employees synced from admin portal')
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
    # Merge ALL attendance sources: app_data records + python cam + flutter app punches
    merged = list(app_data.get('records', []))
    existing_times = {r.get('time') for r in merged}

    # Merge Python camera records
    for pr in py_recs:
        if pr.get('time') not in existing_times:
            merged.append(pr)
            existing_times.add(pr.get('time'))

    # Merge Flutter app punches (face scan + manual approved)
    for ap in app_punches:
        if ap.get('time') not in existing_times:
            merged.append(ap)
            existing_times.add(ap.get('time'))

    return jsonify({
        'ok': True,
        'employees':   app_data.get('employees', []),
        'sites':       app_data.get('sites', []),
        'supervisors': app_supervisors,
        'records':     merged,
        'settings':    app_data.get('settings', {}),
        'ec':          app_data.get('ec', 100),
        'sc':          app_data.get('sc', 20),
        'version':     app_data.get('version', 1)
    })

@app.route('/data', methods=['POST'])
def set_app_data():
    """Device pushes updated app state to server (on any save)."""
    global app_data, app_supervisors
    data = request.json

    # Merge supervisors into the unified app_supervisors list
    incoming_sups = data.pop('supervisors', None)
    if incoming_sups is not None:
        for s in incoming_sups:
            did = s.get('deviceId')
            if did:
                existing = next((x for x in app_supervisors if x['deviceId'] == did), None)
                if existing:
                    existing.update({k: v for k, v in s.items() if k != 'deviceId'})
                else:
                    app_supervisors.append(s)
            # supervisors without deviceId are ERP-only entries; store in app_data
            else:
                app_data.setdefault('supervisors', [])
                if not any(x.get('name') == s.get('name') for x in app_data['supervisors']):
                    app_data['supervisors'].append(s)
        save_supervisors()

    app_data.update({k: v for k, v in data.items() if k != 'supervisors'})
    app_data['version'] = app_data.get('version', 1) + 1
    save_app_data()
    log.info(f'App data saved — {len(data.get("employees",[]))} employees, '
             f'{len(data.get("records",[]))} records (v{app_data["version"]})')
    return jsonify({'ok': True, 'version': app_data['version']})

@app.route('/data/version', methods=['GET'])
def get_data_version():
    """Lightweight check — devices poll this to detect changes from other devices."""
    return jsonify({'ok': True, 'version': app_data.get('version', 1)})


# ── Manual Attendance Requests ────────────────────────────────────────────────
MANUAL_REQS_FILE = os.path.join(DATA_DIR, 'manual_requests.json')
manual_requests = []

def load_manual_requests():
    global manual_requests
    if os.path.exists(MANUAL_REQS_FILE):
        try:
            with open(MANUAL_REQS_FILE, 'r') as f:
                manual_requests = json.load(f)
        except Exception as e:
            log.error(f'Failed to load manual_requests: {e}')

def save_manual_requests():
    try:
        with open(MANUAL_REQS_FILE, 'w') as f:
            json.dump(manual_requests, f, indent=2)
    except Exception as e:
        log.error(f'Failed to save manual_requests: {e}')

@app.route('/manual-request', methods=['POST'])
def submit_manual_request():
    """
    Supervisor submits a manual attendance request with employee photo.
    Body: { empUid, empName, type, siteId, siteName, supervisorDeviceId,
            supervisorName, timestamp, photo_b64 }
    """
    import datetime as dt
    data = request.json
    req_id = f"MR-{int(dt.datetime.now().timestamp()*1000)}"

    req = {
        'id':                 req_id,
        'empUid':             data.get('empUid', ''),
        'empName':            data.get('empName', ''),
        'type':               data.get('type', 'in'),
        'siteId':             data.get('siteId', ''),
        'siteName':           data.get('siteName', ''),
        'supervisorDeviceId': data.get('supervisorDeviceId', ''),
        'supervisorName':     data.get('supervisorName', ''),
        'timestamp':          data.get('timestamp', dt.datetime.now().isoformat()),
        'photo_b64':          data.get('photo_b64', ''),
        'status':             'pending',   # pending | approved | rejected
        'submittedAt':        dt.datetime.now().isoformat(),
        'reviewedAt':         None,
        'reviewNote':         '',
    }
    manual_requests.append(req)
    save_manual_requests()
    log.info(f'Manual request: {req["empName"]} {req["type"].upper()} by {req["supervisorName"]} → {req_id}')
    return jsonify({'ok': True, 'requestId': req_id})

@app.route('/manual-requests', methods=['GET'])
def get_manual_requests():
    """Admin: get all manual attendance requests."""
    status_filter = request.args.get('status')   # pending | approved | rejected | all
    if status_filter and status_filter != 'all':
        filtered = [r for r in manual_requests if r['status'] == status_filter]
    else:
        filtered = manual_requests
    # Sort newest first, strip large photo for list view
    result = []
    for r in reversed(filtered):
        row = {k: v for k, v in r.items() if k != 'photo_b64'}
        row['hasPhoto'] = bool(r.get('photo_b64'))
        result.append(row)
    return jsonify({'ok': True, 'requests': result, 'count': len(result)})

@app.route('/manual-request/<req_id>/photo', methods=['GET'])
def get_manual_request_photo(req_id):
    """Get photo for a specific manual request."""
    req = next((r for r in manual_requests if r['id'] == req_id), None)
    if not req:
        return jsonify({'ok': False, 'error': 'Not found'}), 404
    return jsonify({'ok': True, 'photo_b64': req.get('photo_b64', '')})

@app.route('/manual-request/review', methods=['POST'])
def review_manual_request():
    """
    Admin approves or rejects a manual attendance request.
    Body: { id, action ('approve'|'reject'), note }
    """
    import datetime as dt
    data   = request.json
    req_id = data.get('id')
    action = data.get('action', 'approve')
    note   = data.get('note', '')

    req = next((r for r in manual_requests if r['id'] == req_id), None)
    if not req:
        return jsonify({'ok': False, 'error': 'Request not found'}), 404

    req['status']     = 'approved' if action == 'approve' else 'rejected'
    req['reviewedAt'] = dt.datetime.now().isoformat()
    req['reviewNote'] = note

    # If approved → auto-create attendance punch record
    if action == 'approve':
        punch_rec = {
            'empUid':             req['empUid'],
            'empName':            req['empName'],
            'type':               req['type'],
            'time':               req['timestamp'],
            'siteId':             req['siteId'],
            'siteName':           req['siteName'],
            'supervisorDeviceId': req['supervisorDeviceId'],
            'supervisorName':     req['supervisorName'],
            'source':             'manual-approved',
            'manualRequestId':    req_id,
        }
        app_punches.append(punch_rec)
        save_app_punches()

    save_manual_requests()
    log.info(f'Manual request {req_id}: {action.upper()} by admin')
    return jsonify({'ok': True, 'status': req['status']})


# ── Supervisor Management ─────────────────────────────────────────────────────
SUP_FILE = os.path.join(DATA_DIR, 'app_supervisors.json')
app_supervisors = []   # list of supervisor device registrations

def load_supervisors():
    global app_supervisors
    if os.path.exists(SUP_FILE):
        try:
            with open(SUP_FILE, 'r') as f:
                app_supervisors = json.load(f)
            log.info(f'Loaded {len(app_supervisors)} supervisor(s)')
        except Exception as e:
            log.error(f'Failed to load supervisors: {e}')

def save_supervisors():
    try:
        with open(SUP_FILE, 'w') as f:
            json.dump(app_supervisors, f, indent=2)
    except Exception as e:
        log.error(f'Failed to save supervisors: {e}')

@app.route('/supervisor/register', methods=['POST'])
def supervisor_register():
    """New supervisor installs app and registers device."""
    data      = request.json
    device_id = data.get('deviceId')
    name      = data.get('name', '').strip()
    phone     = data.get('phone', '').strip()

    if not device_id:
        return jsonify({'ok': False, 'error': 'Missing deviceId'}), 400

    # Check if already registered
    existing = next((s for s in app_supervisors if s['deviceId'] == device_id), None)
    if existing:
        return jsonify({'ok': True, 'supervisor': existing, 'isNew': False})

    # Register new supervisor (pending admin approval)
    sup = {
        'deviceId':   device_id,
        'name':       name or 'Pending Name',
        'phone':      phone,
        'approved':   False,
        'canEnroll':  False,
        'role':       'Supervisor',
        'sites':      [],
        'registeredAt': __import__('datetime').datetime.now().isoformat(),
    }
    app_supervisors.append(sup)
    save_supervisors()
    # Keep app_data version counter ticking so ERP detects the change
    app_data['version'] = app_data.get('version', 1) + 1
    save_app_data()
    log.info(f'New supervisor registered: {name} ({device_id})')
    return jsonify({'ok': True, 'supervisor': sup, 'isNew': True})

@app.route('/supervisor/login', methods=['POST'])
def supervisor_login():
    """Supervisor app checks its approval status on launch."""
    device_id = request.json.get('deviceId')
    if not device_id:
        return jsonify({'ok': False, 'error': 'Missing deviceId'}), 400
    sup = next((s for s in app_supervisors if s['deviceId'] == device_id), None)
    if not sup:
        return jsonify({'ok': False, 'error': 'Not registered'})
    return jsonify({'ok': True, 'supervisor': sup})

@app.route('/supervisor/list', methods=['GET'])
def supervisor_list():
    """Admin: get all registered supervisors."""
    return jsonify({'ok': True, 'supervisors': app_supervisors, 'count': len(app_supervisors)})

@app.route('/supervisor/update', methods=['POST'])
def supervisor_update():
    """Admin: approve supervisor, set name, role, sites, enroll permission."""
    data      = request.json
    device_id = data.get('deviceId')
    if not device_id:
        return jsonify({'ok': False, 'error': 'Missing deviceId'}), 400

    sup = next((s for s in app_supervisors if s['deviceId'] == device_id), None)
    if not sup:
        return jsonify({'ok': False, 'error': 'Supervisor not found'})

    # Update fields if provided
    for field in ['name', 'phone', 'role', 'sites', 'approved', 'canEnroll']:
        if field in data:
            sup[field] = data[field]

    save_supervisors()
    log.info(f'Supervisor updated: {sup["name"]} ({device_id}) approved={sup["approved"]} canEnroll={sup["canEnroll"]}')
    return jsonify({'ok': True, 'supervisor': sup})

@app.route('/supervisor/delete', methods=['POST'])
def supervisor_delete():
    """Admin: remove a supervisor."""
    global app_supervisors
    device_id = request.json.get('deviceId')
    app_supervisors = [s for s in app_supervisors if s['deviceId'] != device_id]
    save_supervisors()
    return jsonify({'ok': True})

@app.route('/supervisor/restore', methods=['POST'])
def supervisor_restore():
    """Admin: bulk-restore cached supervisors after Railway restart."""
    global app_supervisors
    data = request.json or {}
    incoming = data.get('supervisors', [])
    restored = 0
    for s in incoming:
        device_id = s.get('deviceId')
        if not device_id:
            continue
        # Only add if not already present
        if not any(x['deviceId'] == device_id for x in app_supervisors):
            app_supervisors.append(s)
            restored += 1
        else:
            # Update existing with latest approved/canEnroll from cache
            for x in app_supervisors:
                if x['deviceId'] == device_id:
                    x.update({k: v for k, v in s.items() if k != 'deviceId'})
                    break
    save_supervisors()
    log.info(f'Supervisor restore: {restored} new, {len(incoming)-restored} updated')
    return jsonify({'ok': True, 'restored': restored, 'total': len(app_supervisors)})

# ── Attendance Punch (App) ────────────────────────────────────────────────────
APP_PUNCHES_FILE = os.path.join(DATA_DIR, 'app_punches.json')
app_punches = []

def load_app_punches():
    global app_punches
    if os.path.exists(APP_PUNCHES_FILE):
        try:
            with open(APP_PUNCHES_FILE, 'r') as f:
                app_punches = json.load(f)
            log.info(f'Loaded {len(app_punches)} app punch record(s)')
        except Exception as e:
            log.error(f'Failed to load app_punches: {e}')

def save_app_punches():
    try:
        with open(APP_PUNCHES_FILE, 'w') as f:
            json.dump(app_punches, f, indent=2)
    except Exception as e:
        log.error(f'Failed to save app_punches: {e}')

@app.route('/app/punch', methods=['POST'])
def app_punch():
    """Flutter app records a CHECK IN or CHECK OUT."""
    data = request.json
    emp_uid    = data.get('empUid')
    emp_name   = data.get('empName', '')
    punch_type = data.get('type', 'in').lower()   # 'in' or 'out'
    site_id    = data.get('siteId', '')
    site_name  = data.get('siteName', '')
    sup_id     = data.get('supervisorDeviceId', '')
    sup_name   = data.get('supervisorName', '')
    timestamp  = data.get('timestamp', __import__('datetime').datetime.now().isoformat())

    if not emp_uid:
        return jsonify({'ok': False, 'error': 'Missing empUid'}), 400

    lat = data.get('lat')
    lng = data.get('lng')

    auto_checked_out = False

    # ── Auto-checkout: if new punch is CHECK IN and employee still has an open IN ──
    if punch_type == 'in':
        emp_recs = sorted(
            [p for p in app_punches if p.get('empUid') == emp_uid],
            key=lambda x: x.get('time', '')
        )
        if emp_recs and emp_recs[-1].get('type') == 'in':
            # Create automatic checkout with same timestamp as the new check-in
            prev = emp_recs[-1]
            auto_out = {
                'empUid':             emp_uid,
                'empName':            emp_name,
                'type':               'out',
                'time':               timestamp,
                'siteId':             prev.get('siteId', site_id),
                'siteName':           prev.get('siteName', site_name),
                'supervisorDeviceId': sup_id,
                'supervisorName':     sup_name,
                'source':             'auto-checkout',
            }
            if lat is not None: auto_out['lat'] = round(float(lat), 6)
            if lng is not None: auto_out['lng'] = round(float(lng), 6)
            app_punches.append(auto_out)
            auto_checked_out = True
            log.info(f'Auto-checkout: {emp_name} (was open IN from {prev.get("time","")})')

    rec = {
        'empUid':             emp_uid,
        'empName':            emp_name,
        'type':               punch_type,
        'time':               timestamp,
        'siteId':             site_id,
        'siteName':           site_name,
        'supervisorDeviceId': sup_id,
        'supervisorName':     sup_name,
        'source':             'flutter-app',
    }
    if lat is not None: rec['lat'] = round(float(lat), 6)
    if lng is not None: rec['lng'] = round(float(lng), 6)

    app_punches.append(rec)
    save_app_punches()
    loc_str = f' [{lat:.4f},{lng:.4f}]' if lat and lng else ' [no GPS]'
    log.info(f'App punch: {emp_name} → {punch_type.upper()} at {site_name} by {sup_name}{loc_str}')
    return jsonify({'ok': True, 'record': rec, 'autoCheckedOut': auto_checked_out})

@app.route('/app/punches', methods=['GET'])
def get_app_punches():
    """Get all Flutter app punch records (with optional date filter)."""
    date_filter = request.args.get('date')   # YYYY-MM-DD
    if date_filter:
        filtered = [r for r in app_punches if r.get('time', '').startswith(date_filter)]
    else:
        filtered = app_punches
    return jsonify({'ok': True, 'records': filtered, 'count': len(filtered)})

@app.route('/app/manhours', methods=['GET'])
def get_manhours():
    """Calculate man hours per employee from app punch records."""
    from datetime import datetime
    date_filter = request.args.get('date')
    records = app_punches
    if date_filter:
        records = [r for r in records if r.get('time', '').startswith(date_filter)]

    # Group by empUid + date
    from collections import defaultdict
    emp_punches = defaultdict(lambda: {'in': [], 'out': []})
    for r in records:
        uid = r.get('empUid', '')
        t   = r.get('type', '')
        emp_punches[uid][t].append(r)

    results = []
    for uid, punches in emp_punches.items():
        ins  = sorted(punches['in'],  key=lambda x: x.get('time', ''))
        outs = sorted(punches['out'], key=lambda x: x.get('time', ''))
        total_mins = 0
        pairs = []
        for i, in_rec in enumerate(ins):
            if i < len(outs):
                try:
                    t_in  = datetime.fromisoformat(in_rec['time'])
                    t_out = datetime.fromisoformat(outs[i]['time'])
                    mins  = max(0, (t_out - t_in).total_seconds() / 60)
                    total_mins += mins
                    pairs.append({'in': in_rec['time'], 'out': outs[i]['time'], 'minutes': mins})
                except Exception:
                    pass
        results.append({
            'empUid':      uid,
            'empName':     ins[0].get('empName', uid) if ins else uid,
            'totalMinutes': total_mins,
            'totalHours':  round(total_mins / 60, 2),
            'pairs':       pairs,
        })

    return jsonify({'ok': True, 'manhours': results})


# ── Boot ─────────────────────────────────────────────────────────────────────
load_db()
load_employees()
load_py_recs()
load_app_data()
load_supervisors()
load_app_punches()
load_manual_requests()

# ── Sites API ────────────────────────────────────────────────────────────────
@app.route('/app/sites', methods=['GET'])
def get_sites():
    """Return current sites list from app_data."""
    return jsonify({'ok': True, 'sites': app_data.get('sites', [])})

@app.route('/app/sites', methods=['POST'])
def set_sites():
    """Bulk-replace sites from ERP sync."""
    global app_data
    sites = request.json.get('sites', [])
    app_data['sites'] = sites
    app_data['version'] = app_data.get('version', 1) + 1
    save_app_data()
    log.info(f'Sites synced from ERP: {len(sites)} sites')
    return jsonify({'ok': True, 'count': len(sites)})

@app.route('/app/sites/<site_id>', methods=['PUT'])
def update_site(site_id):
    """Edit a single site."""
    global app_data
    data  = request.json or {}
    sites = app_data.get('sites', [])
    idx   = next((i for i, s in enumerate(sites) if s.get('id') == site_id), None)
    if idx is None:
        return jsonify({'ok': False, 'error': 'Site not found'}), 404
    sites[idx].update({k: v for k, v in data.items() if k != 'id'})
    app_data['sites'] = sites
    app_data['version'] = app_data.get('version', 1) + 1
    save_app_data()
    log.info(f'Site updated: {site_id}')
    return jsonify({'ok': True, 'site': sites[idx]})


# ── Schedule Storage ──────────────────────────────────────────────────────────
SCHEDULE_FILE = os.path.join(DATA_DIR, 'schedule.json')
schedule_by_date = {}  # date -> { siteId -> [{uid, name, empId, designation}, ...] }

def load_schedule():
    global schedule_by_date
    if os.path.exists(SCHEDULE_FILE):
        try:
            with open(SCHEDULE_FILE, 'r') as f:
                schedule_by_date = json.load(f)
            log.info(f'Loaded schedule for {len(schedule_by_date)} date(s)')
        except Exception as e:
            log.error(f'Failed to load schedule: {e}')

def save_schedule():
    try:
        with open(SCHEDULE_FILE, 'w') as f:
            json.dump(schedule_by_date, f)
    except Exception as e:
        log.error(f'Failed to save schedule: {e}')

load_schedule()  # must be called after function definition above

@app.route('/app/schedule', methods=['GET'])
def get_schedule():
    date    = request.args.get('date', '')
    site_id = request.args.get('siteId', '')
    if not date:
        return jsonify({'ok': False, 'error': 'date required'}), 400
    day = schedule_by_date.get(date, {})
    if site_id:
        return jsonify({'ok': True, 'employees': day.get(site_id, []), 'date': date, 'siteId': site_id})
    return jsonify({'ok': True, 'schedule': day, 'date': date})

@app.route('/app/schedule', methods=['POST'])
def set_schedule():
    data      = request.json
    date      = data.get('date', '')
    site_id   = data.get('siteId', '')
    employees = data.get('employees', [])  # [{uid,name,empId,designation}]
    if not date or not site_id:
        return jsonify({'ok': False, 'error': 'date and siteId required'}), 400
    if date not in schedule_by_date:
        schedule_by_date[date] = {}
    schedule_by_date[date][site_id] = employees
    save_schedule()
    log.info(f'Schedule saved: {date} site={site_id} → {len(employees)} employees')
    return jsonify({'ok': True, 'count': len(employees)})


# ── Attendance Report ─────────────────────────────────────────────────────────
@app.route('/app/report', methods=['GET'])
def get_report():
    from datetime import datetime
    from collections import defaultdict

    start   = request.args.get('start', '')   # YYYY-MM-DD
    end     = request.args.get('end', '')      # YYYY-MM-DD
    site_id = request.args.get('siteId', '')

    def in_range(rec):
        t = rec.get('time', '')
        if start and t < start:
            return False
        if end and t > end + 'T99:99:99':
            return False
        return True

    records = [r for r in app_punches if in_range(r)
               and (not site_id or r.get('siteId') == site_id)]

    emp_lookup = {}
    for e in emp_db:
        uid = e.get('uid') or e.get('id', '')
        if uid:
            emp_lookup[uid] = e

    emp_data = defaultdict(lambda: {'ins': [], 'outs': [], 'name': '', 'siteName': ''})
    for r in records:
        uid = r.get('empUid', '')
        if not uid:
            continue
        if r.get('type') == 'in':
            emp_data[uid]['ins'].append(r)
        else:
            emp_data[uid]['outs'].append(r)
        if r.get('empName'):
            emp_data[uid]['name'] = r['empName']
        if r.get('siteName'):
            emp_data[uid]['siteName'] = r['siteName']

    result = []
    for uid, d in emp_data.items():
        emp  = emp_lookup.get(uid, {})
        ins  = sorted(d['ins'],  key=lambda x: x.get('time', ''))
        outs = sorted(d['outs'], key=lambda x: x.get('time', ''))
        max_pairs  = max(len(ins), len(outs)) if (ins or outs) else 0
        total_mins = 0
        sessions   = []
        for i in range(max_pairs):
            in_rec  = ins[i]  if i < len(ins)  else None
            out_rec = outs[i] if i < len(outs) else None
            mins = 0
            if in_rec and out_rec:
                try:
                    t_in  = datetime.fromisoformat(in_rec['time'].replace('Z', '+00:00'))
                    t_out = datetime.fromisoformat(out_rec['time'].replace('Z', '+00:00'))
                    mins  = max(0, (t_out - t_in).total_seconds() / 60)
                    total_mins += mins
                except Exception:
                    pass
            sessions.append({'checkIn': in_rec, 'checkOut': out_rec, 'minutes': round(mins)})

        result.append({
            'empUid':       uid,
            'empId':        emp.get('empId') or emp.get('id') or uid,
            'empName':      d['name'] or uid,
            'designation':  emp.get('role') or emp.get('designation', ''),
            'dept':         emp.get('dept', ''),
            'siteName':     d['siteName'],
            'totalMinutes': round(total_mins),
            'totalHours':   round(total_mins / 60, 2),
            'sessions':     sessions,
        })

    result.sort(key=lambda x: x['empName'])
    return jsonify({'ok': True, 'report': result, 'count': len(result)})


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
