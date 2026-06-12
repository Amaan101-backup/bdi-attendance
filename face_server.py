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
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
import numpy as np
import base64
import json
import os
import io
import webbrowser
import threading
import urllib.request
import urllib.error
from PIL import Image, ExifTags
import logging

# Import face_recognition safely
try:
    import face_recognition
    FACE_RECOGNITION_AVAILABLE = True
except ImportError as e:
    logging.warning(f'face_recognition not available: {e}')
    FACE_RECOGNITION_AVAILABLE = False

# ── PostgreSQL persistence ────────────────────────────────────────────────────
# When DATABASE_URL is set (Railway PostgreSQL), all data is saved to Postgres.
# Without it, falls back to JSON files (local dev unchanged).

_DB_URL = os.environ.get('DATABASE_URL', '')
if _DB_URL.startswith('postgres://'):          # Railway uses postgres:// prefix
    _DB_URL = _DB_URL.replace('postgres://', 'postgresql://', 1)
USE_DB = bool(_DB_URL)

def _db_connect():
    import psycopg2
    return psycopg2.connect(_DB_URL, connect_timeout=5)

def _db_init():
    """Create kv_store table once on startup."""
    if not USE_DB:
        return
    try:
        conn = _db_connect()
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS kv_store (
                    key        TEXT PRIMARY KEY,
                    value      TEXT NOT NULL,
                    updated_at TIMESTAMPTZ DEFAULT NOW()
                )
            """)
        conn.commit()
        conn.close()
        log.info('✅ PostgreSQL connected — data will persist across restarts')
    except Exception as e:
        log.error(f'❌ DB init failed: {e}')

def _db_load(key, default):
    """Load a value from PostgreSQL by key, return default if not found."""
    if not USE_DB:
        return default
    try:
        conn = _db_connect()
        with conn.cursor() as cur:
            cur.execute("SELECT value FROM kv_store WHERE key = %s", (key,))
            row = cur.fetchone()
        conn.close()
        if row:
            return json.loads(row[0])
        return default
    except Exception as e:
        log.error(f'DB load [{key}] failed: {e}')
        return default

def _db_save(key, data):
    """Save a value to PostgreSQL by key (upsert)."""
    if not USE_DB:
        return
    try:
        conn = _db_connect()
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO kv_store (key, value, updated_at)
                VALUES (%s, %s, NOW())
                ON CONFLICT (key) DO UPDATE
                    SET value = EXCLUDED.value,
                        updated_at = NOW()
            """, (key, json.dumps(data, default=str)))
        conn.commit()
        conn.close()
    except Exception as e:
        log.error(f'DB save [{key}] failed: {e}')

# ── Serve static files from same directory as this script ─────────────────────
BASE_DIR  = os.path.dirname(os.path.abspath(__file__))

# Data directory — /app/data by default so Railway volume mount persists JSON files
DATA_DIR  = os.environ.get('DATA_DIR', os.path.join(BASE_DIR, 'data'))
PORT      = int(os.environ.get('PORT', 5000))

os.makedirs(DATA_DIR, exist_ok=True)

app = Flask(__name__, static_folder=BASE_DIR, static_url_path='')

# ── Security: read secrets from environment ────────────────────────────────────
IMPORT_SECRET  = os.environ.get('IMPORT_SECRET', 'bdi-import-2024')
RESET_SECRET   = os.environ.get('RESET_SECRET',  'bdi-reset-2024')
ADMIN_API_KEY  = os.environ.get('ADMIN_API_KEY', '')
ADMIN_USERNAME = os.environ.get('ADMIN_USERNAME', 'admin')
ADMIN_PASSWORD = os.environ.get('ADMIN_PASSWORD', 'bdi2026')

# Token secret — generated once at startup and stored in DB so it survives restarts
import secrets as _secrets, hmac as _hmac, hashlib as _hashlib, time as _time
from functools import wraps

_TOKEN_SECRET = None   # loaded after DB is ready
_ADMIN_SESSIONS: dict = {}   # token → expiry epoch
_SESSION_TTL = 8 * 3600      # 8 hours

def _load_token_secret():
    global _TOKEN_SECRET
    stored = _db_load('_admin_token_secret', None) if USE_DB else None
    if stored:
        _TOKEN_SECRET = stored
    else:
        _TOKEN_SECRET = _secrets.token_hex(32)
        if USE_DB:
            _db_save('_admin_token_secret', _TOKEN_SECRET)

def _create_session() -> str:
    token = _secrets.token_urlsafe(40)
    _ADMIN_SESSIONS[token] = _time.time() + _SESSION_TTL
    # Prune stale sessions
    now = _time.time()
    expired = [t for t, exp in _ADMIN_SESSIONS.items() if exp < now]
    for t in expired:
        del _ADMIN_SESSIONS[t]
    return token

def _verify_session(token: str) -> bool:
    if not token:
        return False
    exp = _ADMIN_SESSIONS.get(token)
    if not exp:
        return False
    if _time.time() > exp:
        del _ADMIN_SESSIONS[token]
        return False
    return True

def _get_token_from_request() -> str:
    auth = request.headers.get('X-Admin-Token', '')
    if not auth:
        auth = request.headers.get('Authorization', '')
        if auth.startswith('Bearer '):
            auth = auth[7:]
    return auth

def require_admin(f):
    """Decorator: requires a valid admin session token on X-Admin-Token header."""
    @wraps(f)
    def decorated(*args, **kwargs):
        if not _verify_session(_get_token_from_request()):
            return jsonify({'ok': False, 'error': 'Unauthorized — admin login required'}), 401
        return f(*args, **kwargs)
    return decorated

# ── CORS: restrict origins in production ─────────────────────────────────────
_ALLOWED_ORIGINS_ENV = os.environ.get('ALLOWED_ORIGINS', '')
if _ALLOWED_ORIGINS_ENV:
    _CORS_ORIGINS = [o.strip() for o in _ALLOWED_ORIGINS_ENV.split(',') if o.strip()]
else:
    # Default: allow Railway + Netlify + localhost for dev
    _CORS_ORIGINS = [
        'https://bdi-attendance-production-d7e9.up.railway.app',
        'https://admirable-empanada-6f089e.netlify.app',
        'https://neon-mooncake-e6a0c5.netlify.app',
        'https://attendance.bdiportals.com',
        'http://localhost:5000',
        'http://localhost:8080',
        'http://127.0.0.1:5000',
    ]

CORS(app, origins=_CORS_ORIGINS, supports_credentials=False,
     allow_headers=['Content-Type', 'Authorization'],
     methods=['GET', 'POST', 'PUT', 'DELETE', 'OPTIONS'])

@app.after_request
def add_cors_headers(response):
    origin = request.headers.get('Origin', '')
    if origin in _CORS_ORIGINS:
        response.headers['Access-Control-Allow-Origin'] = origin
    response.headers['Access-Control-Allow-Headers'] = 'Content-Type, Authorization'
    response.headers['Access-Control-Allow-Methods'] = 'GET, POST, PUT, DELETE, OPTIONS'
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-Frame-Options'] = 'SAMEORIGIN'
    return response

# ── Rate limiting ─────────────────────────────────────────────────────────────
limiter = Limiter(
    get_remote_address,
    app=app,
    default_limits=[],          # No blanket limit; apply per-route below
    storage_uri='memory://',    # In-memory (resets on restart — good enough)
)

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

@app.route('/admin/login', methods=['POST'])
@limiter.limit('10 per hour')
def admin_login():
    """Server-side admin login — returns a session token."""
    data = request.json or {}
    username = data.get('username', '').strip()
    password = data.get('password', '')
    # Constant-time comparison to prevent timing attacks
    u_ok = _hmac.compare_digest(username.lower(), ADMIN_USERNAME.lower())
    p_ok = _hmac.compare_digest(password, ADMIN_PASSWORD)
    if u_ok and p_ok:
        token = _create_session()
        log.info(f'Admin login from {request.remote_addr}')
        return jsonify({'ok': True, 'token': token, 'ttl': _SESSION_TTL})
    log.warning(f'Failed admin login attempt from {request.remote_addr}')
    return jsonify({'ok': False, 'error': 'Invalid username or password'}), 401

@app.route('/admin/logout', methods=['POST'])
def admin_logout():
    token = _get_token_from_request()
    if token in _ADMIN_SESSIONS:
        del _ADMIN_SESSIONS[token]
    return jsonify({'ok': True})

@app.route('/admin/verify', methods=['GET'])
def admin_verify():
    """Check if the current session token is still valid."""
    return jsonify({'ok': _verify_session(_get_token_from_request())})

@app.route('/attendance-bridge.js')
def attendance_bridge():
    resp = send_from_directory(BASE_DIR, 'attendance-bridge.js')
    resp.headers['Content-Type'] = 'application/javascript'
    resp.headers['Access-Control-Allow-Origin'] = '*'
    return resp

# ── Storage ─────────────────────────────────────────────────────────────────
ENCODINGS_FILE = os.path.join(DATA_DIR, 'face_encodings.json')
PRESET_FILE    = os.path.join(BASE_DIR, 'face_encodings_preset.json')
face_db = {}           # uid → list of 128-dim encodings (numpy arrays)
face_enroll_dates = {} # uid → ISO timestamp of first enrollment

def _load_enroll_dates():
    global face_enroll_dates
    face_enroll_dates = _db_load('face_enroll_dates', {}) if USE_DB else {}

def _save_enroll_dates():
    if USE_DB:
        _db_save('face_enroll_dates', face_enroll_dates)

def _touch_enroll_date(uid):
    """Set enrollment date for uid if not already recorded."""
    if uid not in face_enroll_dates:
        from datetime import datetime as _dt
        face_enroll_dates[uid] = _dt.now().isoformat()
        _save_enroll_dates()

def load_db():
    global face_db
    raw = {}
    if USE_DB:
        raw = _db_load('face_encodings', {})
        if raw:
            log.info(f'Loaded {len(raw)} enrolled employee(s) from PostgreSQL')
    if not raw and os.path.exists(ENCODINGS_FILE):
        try:
            with open(ENCODINGS_FILE, 'r') as f:
                raw = json.load(f)
            log.info(f'Loaded {len(raw)} enrolled employee(s) from disk')
        except Exception as e:
            log.error(f'Failed to load encodings: {e}')
    if not raw and os.path.exists(PRESET_FILE):
        try:
            with open(PRESET_FILE, 'r') as f:
                raw = json.load(f)
            if raw:
                log.info(f'Loaded {len(raw)} enrolled employee(s) from PRESET')
        except Exception as e:
            log.error(f'Failed to load preset: {e}')
    face_db = {uid: [np.array(enc) for enc in encs] for uid, encs in raw.items()} if raw else {}
    if raw:
        save_db()  # ensure DB and file are both up to date

def save_preset_and_github():
    """Save face encodings to preset file locally, then commit to GitHub if configured.
    Called automatically after every enroll/delete so data is never lost on redeploy."""
    raw = {uid: [enc.tolist() for enc in encs] for uid, encs in face_db.items()}
    # Always save to preset file (baked into next Docker image via COPY)
    try:
        with open(PRESET_FILE, 'w') as f:
            json.dump(raw, f)
        log.info(f'Auto-saved {len(raw)} face(s) to preset file')
    except Exception as e:
        log.error(f'Failed to auto-save preset: {e}')

    # Auto-commit to GitHub if env vars are set
    github_token  = os.environ.get('GITHUB_TOKEN', '')
    github_repo   = os.environ.get('GITHUB_REPO', '')
    github_branch = os.environ.get('GITHUB_BRANCH', 'master')
    if not github_token or not github_repo:
        return  # GitHub not configured — silent skip

    file_path    = 'face_encodings_preset.json'
    api_url      = f'https://api.github.com/repos/{github_repo}/contents/{file_path}'
    auth_headers = {
        'Authorization': f'token {github_token}',
        'Accept':        'application/vnd.github.v3+json',
        'Content-Type':  'application/json',
        'User-Agent':    'BDI-Attendance-Server'
    }
    sha = None
    try:
        req = urllib.request.Request(f'{api_url}?ref={github_branch}', headers=auth_headers)
        with urllib.request.urlopen(req) as r:
            sha = json.loads(r.read()).get('sha')
    except urllib.error.HTTPError as e:
        if e.code != 404:
            log.warning(f'GitHub get-sha returned {e.code}')
    except Exception:
        pass

    encoded_content = base64.b64encode(json.dumps(raw, indent=2).encode()).decode()
    payload = {'message': f'Auto-backup: {len(raw)} face enrollment(s)', 'content': encoded_content, 'branch': github_branch}
    if sha:
        payload['sha'] = sha
    try:
        body = json.dumps(payload).encode()
        req  = urllib.request.Request(api_url, data=body, headers=auth_headers, method='PUT')
        with urllib.request.urlopen(req) as r:
            resp_data = json.loads(r.read())
        log.info(f'Auto GitHub backup committed: {resp_data.get("commit", {}).get("html_url", "")}')
    except Exception as e:
        log.error(f'Auto GitHub backup failed: {e}')


def save_db():
    raw = {uid: [enc.tolist() for enc in encs] for uid, encs in face_db.items()}
    _db_save('face_encodings', raw)
    try:
        with open(ENCODINGS_FILE, 'w') as f:
            json.dump(raw, f)
    except Exception as e:
        if not USE_DB:
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
@limiter.limit('60 per hour')            # enroll — one-time per employee
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

        _touch_enroll_date(uid)
        save_db()
        log.info(f'Enrolled {name} ({uid}) — {len(face_db[uid])} sample(s) total')
        # Auto-save preset + GitHub backup so enrollment survives next deploy
        threading.Thread(target=save_preset_and_github, daemon=True).start()
        return jsonify({'ok': True, 'uid': uid, 'samples': len(face_db[uid])})

    except Exception as e:
        log.error(f'Enroll error: {e}')
        return jsonify({'ok': False, 'error': str(e)}), 500


@app.route('/recognize', methods=['POST'])
@limiter.limit('30 per minute')          # max 30 face scans/min per IP
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


@app.route('/import-faces', methods=['POST'])
@require_admin
@limiter.limit('30 per hour')
def import_faces():
    """
    Per-employee face import from admin panel.
    Body: { "uid_or_empId": [[...128 floats...], ...], ... }
    No secret required — called from trusted admin UI.
    """
    data = request.json
    if not data or not isinstance(data, dict):
        return jsonify({'ok': False, 'error': 'Expected JSON object { uid: [[...encodings...]] }'}), 400

    count_new = 0
    for uid, encs in data.items():
        if not isinstance(encs, list) or not encs:
            continue
        try:
            numpy_encs = [np.array(e) for e in encs]
        except Exception as e:
            return jsonify({'ok': False, 'error': f'Invalid encoding for {uid}: {e}'}), 400

        # Replace existing encodings for this employee
        face_db[uid] = numpy_encs[-5:]   # keep max 5 samples
        _touch_enroll_date(uid)
        count_new += 1

    if count_new == 0:
        return jsonify({'ok': False, 'error': 'No valid encodings found in payload'}), 400

    save_db()
    log.info(f'Imported faces via /import-faces: {count_new} employee(s)')
    threading.Thread(target=save_preset_and_github, daemon=True).start()
    return jsonify({'ok': True, 'imported': count_new, 'total': len(face_db)})


@app.route('/import-encodings', methods=['POST'])
@require_admin
@limiter.limit('10 per hour')
def import_encodings():
    """
    Bulk-import face encodings from face_encodings.json format.
    Body: { encodings: { uid: [[...128 floats...], ...], ... }, secret: 'bdi-import' }
    Used to upload local Python encodings to the cloud server.
    """
    data = request.json
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
        _touch_enroll_date(uid)

    save_db()
    log.info(f'Imported encodings: {len(incoming)} employees ({count_new} new)')
    return jsonify({'ok': True, 'imported': len(incoming), 'new': count_new, 'total': len(face_db)})


@app.route('/export-encodings', methods=['GET'])
@require_admin
@limiter.limit('10 per hour')
def export_encodings():
    """Export all face encodings as JSON (for backup/migration)."""
    raw = {uid: [enc.tolist() for enc in encs] for uid, encs in face_db.items()}
    return jsonify({'ok': True, 'encodings': raw, 'count': len(raw)})

@app.route('/faces/export', methods=['GET'])
@require_admin
def faces_export():
    """Admin: export current face encodings for preset/backup download."""
    raw = {uid: [enc.tolist() for enc in encs] for uid, encs in face_db.items()}
    return jsonify({'ok': True, 'encodings': raw, 'count': len(raw)})

@app.route('/faces/backup', methods=['POST'])
@require_admin
def faces_backup():
    """Save current encodings as preset on server AND commit to GitHub."""
    raw = {uid: [enc.tolist() for enc in encs] for uid, encs in face_db.items()}
    count = len(raw)

    # ── 1. Save locally as preset (survives next deploy via Docker COPY) ──────
    local_ok = False
    try:
        with open(PRESET_FILE, 'w') as f:
            json.dump(raw, f)
        local_ok = True
        log.info(f'Saved {count} face preset(s) to {PRESET_FILE}')
    except Exception as e:
        log.error(f'Failed to save preset locally: {e}')

    # ── 2. Commit face_encodings_preset.json to GitHub ────────────────────────
    github_token  = os.environ.get('GITHUB_TOKEN', '')
    github_repo   = os.environ.get('GITHUB_REPO', '')    # e.g. "owner/repo"
    github_branch = os.environ.get('GITHUB_BRANCH', 'master')

    if not github_token or not github_repo:
        return jsonify({
            'ok': True, 'count': count,
            'local': local_ok, 'github': False,
            'github_reason': 'GITHUB_TOKEN or GITHUB_REPO not set in Railway env vars'
        })

    file_path    = 'face_encodings_preset.json'
    api_url      = f'https://api.github.com/repos/{github_repo}/contents/{file_path}'
    auth_headers = {
        'Authorization': f'token {github_token}',
        'Accept': 'application/vnd.github.v3+json',
        'Content-Type': 'application/json',
        'User-Agent': 'BDI-Attendance-Server'
    }

    # Get current file SHA (required for update, omit for create)
    sha = None
    try:
        req = urllib.request.Request(f'{api_url}?ref={github_branch}', headers=auth_headers)
        with urllib.request.urlopen(req) as r:
            sha = json.loads(r.read()).get('sha')
    except urllib.error.HTTPError as e:
        if e.code != 404:
            log.warning(f'GitHub get-sha returned {e.code}')

    # Build commit payload
    encoded_content = base64.b64encode(json.dumps(raw, indent=2).encode()).decode()
    payload = {
        'message': f'Auto-backup: {count} face enrollment(s)',
        'content': encoded_content,
        'branch':  github_branch,
    }
    if sha:
        payload['sha'] = sha

    try:
        body = json.dumps(payload).encode()
        req  = urllib.request.Request(api_url, data=body, headers=auth_headers, method='PUT')
        with urllib.request.urlopen(req) as r:
            resp_data = json.loads(r.read())
        commit_url = resp_data.get('commit', {}).get('html_url', '')
        log.info(f'GitHub backup committed: {commit_url}')
        return jsonify({'ok': True, 'count': count, 'local': local_ok, 'github': True, 'commit_url': commit_url})
    except urllib.error.HTTPError as e:
        err = e.read().decode()
        log.error(f'GitHub commit failed {e.code}: {err}')
        return jsonify({'ok': True, 'count': count, 'local': local_ok, 'github': False, 'github_reason': f'HTTP {e.code}: {err[:200]}'})
    except Exception as e:
        log.error(f'GitHub backup error: {e}')
        return jsonify({'ok': True, 'count': count, 'local': local_ok, 'github': False, 'github_reason': str(e)})


@app.route('/backup/full', methods=['GET'])
def backup_full():
    """Export complete server snapshot as one JSON — all data needed to fully restore."""
    raw_faces = {uid: [enc.tolist() for enc in encs] for uid, encs in face_db.items()}
    snapshot = {
        'version':         '2.0',
        'timestamp':       __import__('datetime').datetime.utcnow().isoformat() + 'Z',
        'type':            'bdi_full_backup',
        'face_encodings':  raw_faces,
        'employees':       app_data.get('employees', []),
        'sites':           app_data.get('sites', []),
        'records':         app_data.get('records', []),
        'settings':        app_data.get('settings', {}),
        'ec':              app_data.get('ec', 100),
        'sc':              app_data.get('sc', 20),
        'supervisors':     app_supervisors,
        'schedule':        schedule_by_date,
        'app_punches':     app_punches,
        'manual_requests': manual_requests,
    }
    return jsonify({'ok': True, 'backup': snapshot, 'stats': {
        'employees':   len(snapshot['employees']),
        'sites':       len(snapshot['sites']),
        'face_encodings': len(raw_faces),
        'supervisors': len(app_supervisors),
        'app_punches': len(app_punches),
        'schedule_dates': len(schedule_by_date),
        'manual_requests': len(manual_requests),
    }})

@app.route('/backup/restore', methods=['POST'])
@require_admin
def backup_restore():
    """Restore all server data from a /backup/full snapshot."""
    global app_data, app_supervisors, schedule_by_date, app_punches, manual_requests, face_db
    data = request.json or {}
    bk   = data.get('backup', data)  # accept top-level or wrapped

    errors = []

    # Face encodings
    raw_faces = bk.get('face_encodings', {})
    if raw_faces:
        try:
            face_db = {uid: [np.array(enc) for enc in encs] for uid, encs in raw_faces.items()}
            save_db()
            log.info(f'Restored {len(face_db)} face encoding(s)')
        except Exception as e:
            errors.append(f'face_encodings: {e}')

    # app_data (employees, sites, records, settings)
    for field in ['employees', 'sites', 'records', 'settings', 'ec', 'sc']:
        if field in bk:
            app_data[field] = bk[field]
    app_data['version'] = app_data.get('version', 1) + 1
    save_app_data()

    # Supervisors
    if 'supervisors' in bk:
        app_supervisors = bk['supervisors']
        save_supervisors()

    # Schedule
    if 'schedule' in bk:
        schedule_by_date = bk['schedule']
        save_schedule()

    # App punches
    if 'app_punches' in bk:
        app_punches = bk['app_punches']
        save_app_punches()

    # Manual requests
    if 'manual_requests' in bk:
        manual_requests = bk['manual_requests']
        save_manual_requests()

    log.info('Full server restore complete')
    return jsonify({
        'ok': True,
        'errors': errors,
        'restored': {
            'employees':   len(app_data.get('employees', [])),
            'sites':       len(app_data.get('sites', [])),
            'face_encodings': len(face_db),
            'supervisors': len(app_supervisors),
            'app_punches': len(app_punches),
            'schedule_dates': len(schedule_by_date),
        }
    })

@app.route('/backup/github', methods=['POST'])
@require_admin
def backup_github():
    """Commit full backup JSON to GitHub repo."""
    data     = request.json or {}
    filename = data.get('filename', 'bdi-full-backup.json')

    # Build snapshot
    raw_faces = {uid: [enc.tolist() for enc in encs] for uid, encs in face_db.items()}
    snapshot  = {
        'version':         '2.0',
        'timestamp':       __import__('datetime').datetime.utcnow().isoformat() + 'Z',
        'type':            'bdi_full_backup',
        'face_encodings':  raw_faces,
        'employees':       app_data.get('employees', []),
        'sites':           app_data.get('sites', []),
        'records':         app_data.get('records', []),
        'settings':        app_data.get('settings', {}),
        'supervisors':     app_supervisors,
        'schedule':        schedule_by_date,
        'app_punches':     app_punches,
        'manual_requests': manual_requests,
    }

    github_token  = os.environ.get('GITHUB_TOKEN', '')
    github_repo   = os.environ.get('GITHUB_REPO', '')
    github_branch = os.environ.get('GITHUB_BRANCH', 'master')

    if not github_token or not github_repo:
        return jsonify({'ok': False, 'reason': 'GITHUB_TOKEN or GITHUB_REPO not set in Railway env vars'})

    api_url = f'https://api.github.com/repos/{github_repo}/contents/{filename}'
    headers = {
        'Authorization': f'token {github_token}',
        'Accept': 'application/vnd.github.v3+json',
        'Content-Type': 'application/json',
        'User-Agent': 'BDI-Attendance-Server',
    }

    sha = None
    try:
        req = urllib.request.Request(f'{api_url}?ref={github_branch}', headers=headers)
        with urllib.request.urlopen(req) as r:
            sha = json.loads(r.read()).get('sha')
    except urllib.error.HTTPError as e:
        if e.code != 404:
            log.warning(f'GitHub get-sha {e.code}')

    content  = base64.b64encode(json.dumps(snapshot, indent=2).encode()).decode()
    emp_cnt  = len(snapshot['employees'])
    face_cnt = len(raw_faces)
    payload  = {
        'message': f'Auto-backup: {emp_cnt} employees, {face_cnt} faces – {snapshot["timestamp"][:10]}',
        'content': content,
        'branch':  github_branch,
    }
    if sha:
        payload['sha'] = sha

    try:
        body = json.dumps(payload).encode()
        req  = urllib.request.Request(api_url, data=body, headers=headers, method='PUT')
        with urllib.request.urlopen(req) as r:
            resp = json.loads(r.read())
        commit_url = resp.get('commit', {}).get('html_url', '')
        log.info(f'Full backup committed: {commit_url}')
        return jsonify({'ok': True, 'commit_url': commit_url, 'filename': filename,
                        'stats': {'employees': emp_cnt, 'faces': face_cnt}})
    except urllib.error.HTTPError as e:
        err = e.read().decode()
        return jsonify({'ok': False, 'reason': f'HTTP {e.code}: {err[:300]}'})
    except Exception as e:
        return jsonify({'ok': False, 'reason': str(e)})


@app.route('/delete', methods=['POST'])
@require_admin
@limiter.limit('60 per hour')
def delete():
    """Remove a person's encodings from the database."""
    uid = request.json.get('uid')
    if uid in face_db:
        del face_db[uid]
        save_db()
        log.info(f'Deleted encodings for {uid}')
        threading.Thread(target=save_preset_and_github, daemon=True).start()
        return jsonify({'ok': True})
    return jsonify({'ok': False, 'error': 'UID not found'})


@app.route('/list', methods=['GET'])
@require_admin
def list_enrolled():
    return jsonify({
        'ok': True,
        'enrolled':      {uid: len(encs) for uid, encs in face_db.items()},
        'enroll_dates':  face_enroll_dates,
    })


@app.route('/rename-face', methods=['POST'])
@require_admin
@limiter.limit('60 per hour')
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
    if USE_DB:
        emp_db = _db_load('employees', [])
        if emp_db:
            log.info(f'Loaded {len(emp_db)} employees from PostgreSQL')
            return
    if os.path.exists(EMPLOYEES_FILE):
        try:
            with open(EMPLOYEES_FILE, 'r') as f:
                emp_db = json.load(f)
            log.info(f'Loaded {len(emp_db)} employees from disk')
        except Exception as e:
            log.error(f'Failed to load employees.json: {e}')

def save_employees():
    _db_save('employees', emp_db)
    try:
        with open(EMPLOYEES_FILE, 'w') as f:
            json.dump(emp_db, f, indent=2)
    except Exception as e:
        if not USE_DB:
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
@require_admin
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
    if USE_DB:
        py_recs = _db_load('py_recs', [])
        if py_recs:
            log.info(f'Loaded {len(py_recs)} python attendance record(s) from PostgreSQL')
            return
    if os.path.exists(PY_RECS_FILE):
        try:
            with open(PY_RECS_FILE, 'r') as f:
                py_recs = json.load(f)
            log.info(f'Loaded {len(py_recs)} python attendance record(s) from disk')
        except Exception as e:
            log.error(f'Failed to load python_attendance.json: {e}')
            py_recs = []

def save_py_recs():
    _db_save('py_recs', py_recs)
    try:
        with open(PY_RECS_FILE, 'w') as f:
            json.dump(py_recs, f, indent=2)
    except Exception as e:
        if not USE_DB:
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
@require_admin
@limiter.limit('5 per hour')
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
    if USE_DB:
        app_data = _db_load('app_data', {})
        if app_data:
            log.info(f'Loaded app data from PostgreSQL '
                     f'({len(app_data.get("employees",[]))} employees, '
                     f'{len(app_data.get("records",[]))} records)')
            return
    if os.path.exists(APP_DATA_FILE):
        try:
            with open(APP_DATA_FILE, 'r') as f:
                app_data = json.load(f)
            log.info(f'Loaded app data from disk '
                     f'({len(app_data.get("employees",[]))} employees)')
        except Exception as e:
            log.error(f'Failed to load app_data.json: {e}')
            app_data = {}

def save_app_data():
    _db_save('app_data', app_data)
    try:
        with open(APP_DATA_FILE, 'w') as f:
            json.dump(app_data, f)
    except Exception as e:
        if not USE_DB:
            log.error(f'Failed to save app_data.json: {e}')

@app.route('/data', methods=['GET'])
@require_admin
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
@require_admin
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


# ── Full system reset (wipe all employees, sites, faces, punches) ─────────────
@app.route('/sys/reset-all', methods=['POST'])
@require_admin
@limiter.limit('5 per hour')
def reset_all():
    """
    Wipe ALL data: employees, sites, face encodings, attendance records, schedules.
    Supervisors are kept so the app remains accessible.
    """
    global face_db, emp_db, app_data, app_punches, schedule_by_date

    secret = request.json.get('secret', '') if request.json else ''
    if secret != RESET_SECRET:
        return jsonify({'ok': False, 'error': 'Invalid secret'}), 403

    # 1. Clear face encodings (in-memory + disk)
    face_db = {}
    save_db()
    # Also wipe preset so redeploys start clean
    try:
        with open(PRESET_FILE, 'w') as f:
            import json as _json
            _json.dump({}, f)
    except Exception as e:
        log.warning(f'Could not clear preset file: {e}')

    # 2. Clear employees
    emp_db = []
    save_employees()

    # 3. Clear sites + records in app_data (keep settings & supervisors)
    app_data['employees'] = []
    app_data['sites']     = []
    app_data['records']   = []
    app_data['version']   = app_data.get('version', 1) + 1
    save_app_data()

    # 4. Clear app punches
    app_punches = []
    try:
        with open(APP_PUNCHES_FILE, 'w') as f:
            import json as _json
            _json.dump([], f)
    except Exception as e:
        log.warning(f'Could not clear app_punches file: {e}')

    # 5. Clear schedule
    schedule_by_date = {}
    save_schedule()

    # 6. Auto-backup empty state to GitHub preset
    threading.Thread(target=save_preset_and_github, daemon=True).start()

    log.info('=== FULL SYSTEM RESET performed via /admin/reset-all ===')
    return jsonify({
        'ok': True,
        'message': 'All employees, sites, face encodings and attendance records cleared.',
        'version': app_data.get('version', 1)
    })


# ── Manual Attendance Requests ────────────────────────────────────────────────
MANUAL_REQS_FILE = os.path.join(DATA_DIR, 'manual_requests.json')
manual_requests = []

def load_manual_requests():
    global manual_requests
    if USE_DB:
        manual_requests = _db_load('manual_requests', [])
        if manual_requests:
            log.info(f'Loaded {len(manual_requests)} manual request(s) from PostgreSQL')
            return
    if os.path.exists(MANUAL_REQS_FILE):
        try:
            with open(MANUAL_REQS_FILE, 'r') as f:
                manual_requests = json.load(f)
        except Exception as e:
            log.error(f'Failed to load manual_requests: {e}')

def save_manual_requests():
    _db_save('manual_requests', manual_requests)
    try:
        with open(MANUAL_REQS_FILE, 'w') as f:
            json.dump(manual_requests, f, indent=2)
    except Exception as e:
        if not USE_DB:
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

    lat = data.get('lat')
    lng = data.get('lng')
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
        'lat':                round(float(lat), 6) if lat is not None else None,
        'lng':                round(float(lng), 6) if lng is not None else None,
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
    if USE_DB:
        app_supervisors = _db_load('supervisors', [])
        if app_supervisors:
            log.info(f'Loaded {len(app_supervisors)} supervisor(s) from PostgreSQL')
            return
    if os.path.exists(SUP_FILE):
        try:
            with open(SUP_FILE, 'r') as f:
                app_supervisors = json.load(f)
            log.info(f'Loaded {len(app_supervisors)} supervisor(s) from disk')
        except Exception as e:
            log.error(f'Failed to load supervisors: {e}')

def save_supervisors():
    _db_save('supervisors', app_supervisors)
    try:
        with open(SUP_FILE, 'w') as f:
            json.dump(app_supervisors, f, indent=2)
    except Exception as e:
        if not USE_DB:
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
    if USE_DB:
        app_punches = _db_load('app_punches', [])
        if app_punches:
            log.info(f'Loaded {len(app_punches)} app punch record(s) from PostgreSQL')
            return
    if os.path.exists(APP_PUNCHES_FILE):
        try:
            with open(APP_PUNCHES_FILE, 'r') as f:
                app_punches = json.load(f)
            log.info(f'Loaded {len(app_punches)} app punch record(s) from disk')
        except Exception as e:
            log.error(f'Failed to load app_punches: {e}')

def save_app_punches():
    _db_save('app_punches', app_punches)
    try:
        with open(APP_PUNCHES_FILE, 'w') as f:
            json.dump(app_punches, f, indent=2)
    except Exception as e:
        if not USE_DB:
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

    from datetime import datetime as _dt
    _today = _dt.now().strftime('%Y-%m-%d')

    # ── Guard: never allow a checkout unless the employee checked in today ──
    if punch_type == 'out':
        today_recs = sorted(
            [p for p in app_punches if p.get('empUid') == emp_uid and p.get('time', '').startswith(_today)],
            key=lambda x: x.get('time', '')
        )
        if not today_recs or today_recs[-1].get('type') != 'in':
            return jsonify({'ok': False, 'error': 'no_checkin', 'message': 'Employee has not checked in today'}), 400

    # ── Auto-checkout: close any open check-in before recording a new one ──
    # Same-day open IN → auto-checkout 1 second before the new IN timestamp.
    # Previous-day open IN → auto-checkout at 23:59:59 of that day.
    if punch_type == 'in':
        from datetime import datetime as _dt2, timedelta as _td
        emp_recs = sorted(
            [p for p in app_punches if p.get('empUid') == emp_uid],
            key=lambda x: x.get('time', '')
        )
        if emp_recs:
            last = emp_recs[-1]
            if last.get('type') == 'in':
                last_date = last.get('time', '')[:10]   # YYYY-MM-DD
                if last_date < _today:
                    # Previous day — stamp at end of that day
                    auto_time = last_date + 'T23:59:59'
                else:
                    # Same day — stamp 1 second before the new IN so records are distinct
                    try:
                        ts_dt = _dt2.fromisoformat(timestamp.replace('Z',''))
                        auto_time = (ts_dt - _td(seconds=1)).isoformat()
                    except Exception:
                        auto_time = timestamp
                auto_out = {
                    'empUid':             emp_uid,
                    'empName':            emp_name,
                    'type':               'out',
                    'time':               auto_time,
                    'siteId':             last.get('siteId', site_id),
                    'siteName':           last.get('siteName', site_name),
                    'supervisorDeviceId': sup_id,
                    'supervisorName':     sup_name,
                    'source':             'auto-checkout',
                }
                if lat is not None: auto_out['lat'] = round(float(lat), 6)
                if lng is not None: auto_out['lng'] = round(float(lng), 6)
                app_punches.append(auto_out)
                auto_checked_out = True
                log.info(f'Auto-checkout: {emp_name} (open IN from {last.get("time","")})')

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
_db_init()          # create kv_store table if using PostgreSQL
load_db()
_load_enroll_dates()
_load_token_secret()
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
    if USE_DB:
        schedule_by_date = _db_load('schedule', {})
        if schedule_by_date:
            log.info(f'Loaded schedule for {len(schedule_by_date)} date(s) from PostgreSQL')
            return
    if os.path.exists(SCHEDULE_FILE):
        try:
            with open(SCHEDULE_FILE, 'r') as f:
                schedule_by_date = json.load(f)
            log.info(f'Loaded schedule for {len(schedule_by_date)} date(s) from disk')
        except Exception as e:
            log.error(f'Failed to load schedule: {e}')

def save_schedule():
    _db_save('schedule', schedule_by_date)
    try:
        with open(SCHEDULE_FILE, 'w') as f:
            json.dump(schedule_by_date, f)
    except Exception as e:
        if not USE_DB:
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


# ── Last punch type for a user (face-kiosk) ───────────────────────────────────
@app.route('/app/last-punch-type', methods=['GET'])
def last_punch_type():
    """Return the last punch type ('in' or 'out') for an employee UID — today only.
    If no punch today, return 'out' so the next scan becomes a check-in.
    """
    uid = request.args.get('uid', '')
    if not uid:
        return jsonify({'ok': False, 'error': 'Missing uid'}), 400

    from datetime import datetime as _dt
    today = _dt.now().strftime('%Y-%m-%d')

    today_recs = sorted(
        [p for p in app_punches if p.get('empUid') == uid and p.get('time', '').startswith(today)],
        key=lambda x: x.get('time', '')
    )
    if today_recs:
        return jsonify({'ok': True, 'type': today_recs[-1].get('type', 'out'), 'uid': uid})
    return jsonify({'ok': True, 'type': 'out', 'uid': uid})   # no punch today → default 'out' so next = 'in'


# ── Live dashboard ─────────────────────────────────────────────────────────────
@app.route('/app/dashboard', methods=['GET'])
def get_dashboard():
    """Real-time IN/OUT status per site for today."""
    from datetime import datetime as _dt
    today = _dt.now().strftime('%Y-%m-%d')

    today_punches = [p for p in app_punches if p.get('time', '').startswith(today)]

    # Build last-punch-type per employee
    last_punch = {}   # empUid → latest punch record
    for p in sorted(today_punches, key=lambda x: x.get('time', '')):
        last_punch[p.get('empUid', '')] = p

    # Group by site
    site_stats = {}
    for uid, punch in last_punch.items():
        site_id   = punch.get('siteId', 'unknown')
        site_name = punch.get('siteName', site_id)
        ptype     = punch.get('type', 'out')
        emp_name  = punch.get('empName', uid)

        if site_id not in site_stats:
            site_stats[site_id] = {
                'site_id': site_id, 'site_name': site_name,
                'in_count': 0, 'out_count': 0, 'currently_in': [],
                'last_activity': '',
            }
        s = site_stats[site_id]
        if ptype == 'in':
            s['in_count'] += 1
            s['currently_in'].append(emp_name)
        else:
            s['out_count'] += 1
        t = punch.get('time', '')
        if t > s['last_activity']:
            s['last_activity'] = t

    return jsonify({
        'ok': True,
        'as_of': _dt.now().isoformat(),
        'total_in':  sum(s['in_count']  for s in site_stats.values()),
        'total_out': sum(s['out_count'] for s in site_stats.values()),
        'sites': list(site_stats.values()),
    })


# ── Liveness detection (Android/iOS native path) ──────────────────────────────
@app.route('/liveness', methods=['POST'])
@limiter.limit('60 per minute')
def liveness_check():
    """
    Accepts 2 JPEG frames (base64), checks eye-aspect-ratio change.
    Returns {ok, live, reason}.
    """
    data   = request.json or {}
    frames = data.get('frames', [])

    if len(frames) < 2:
        return jsonify({'ok': True, 'live': True, 'reason': 'Not enough frames — skipped'})

    if not FACE_RECOGNITION_AVAILABLE:
        return jsonify({'ok': True, 'live': True, 'reason': 'face_recognition not available'})

    try:
        from PIL import Image as _Image
        import io as _io

        def _decode(b64_str):
            raw = b64_str.split(',')[-1]
            img = _Image.open(_io.BytesIO(base64.b64decode(raw))).convert('RGB')
            return np.array(img)

        def _ear(eye_pts):
            """Eye Aspect Ratio — lower when eye is closed."""
            import numpy as _np
            pts = _np.array(eye_pts)
            # vertical distances
            A = _np.linalg.norm(pts[1] - pts[5])
            B = _np.linalg.norm(pts[2] - pts[4])
            # horizontal distance
            C = _np.linalg.norm(pts[0] - pts[3])
            return (A + B) / (2.0 * C) if C > 0 else 0

        ears = []
        for b64 in frames[:2]:
            arr       = _decode(b64)
            landmarks = face_recognition.face_landmarks(arr)
            if not landmarks:
                return jsonify({'ok': True, 'live': False,
                                'reason': 'No face detected — please face the camera directly'})
            lm  = landmarks[0]
            ear = (_ear(lm.get('left_eye', [])) + _ear(lm.get('right_eye', []))) / 2
            ears.append(ear)

        ear_diff = abs(ears[0] - ears[1])
        log.info(f'Liveness EAR: {ears[0]:.3f} → {ears[1]:.3f}  diff={ear_diff:.3f}')

        if ear_diff > 0.04:
            return jsonify({'ok': True, 'live': True,
                            'reason': f'Blink detected (EAR Δ={ear_diff:.3f})'})
        else:
            return jsonify({'ok': True, 'live': False,
                            'reason': 'Please blink naturally and try again'})

    except Exception as e:
        log.warning(f'Liveness check error: {e}')
        # Fail-open: don't block attendance on liveness error
        return jsonify({'ok': True, 'live': True, 'reason': f'Check skipped: {e}'})


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
