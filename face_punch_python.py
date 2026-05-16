"""
BDI Attendance — Python Face Punch Terminal
============================================
Stand in front of camera → face is recognized → attendance logged.
Saves to attendance_log.csv (opens in Excel).

CONTROLS:
  Q or ESC = Quit
"""

import sys, os, time, json, csv
from datetime import datetime

print("=" * 55)
print("  BDI Face Punch Terminal")
print("=" * 55)

try:
    import cv2
    print(f"  [OK] OpenCV {cv2.__version__}")
except ImportError:
    print("  [ERROR] OpenCV not installed. Run: pip install opencv-python")
    input("\nPress ENTER to exit...")
    sys.exit(1)

try:
    import face_recognition
    import numpy as np
    print("  [OK] face_recognition (dlib)")
except ImportError:
    print("  [ERROR] face_recognition not installed.")
    print("          Run: pip install face_recognition")
    input("\nPress ENTER to exit...")
    sys.exit(1)

import numpy as np
print("=" * 55)

# ── Paths ─────────────────────────────────────────────────────────────────────
BASE_DIR       = os.path.dirname(os.path.abspath(__file__))
ENCODINGS_FILE = os.path.join(BASE_DIR, 'face_encodings.json')
LOG_FILE       = os.path.join(BASE_DIR, 'attendance_log.csv')

# ── Settings ──────────────────────────────────────────────────────────────────
THRESHOLD       = 0.50   # lower = stricter match (0.4–0.6 is good range)
CONFIRM_FRAMES  = 4      # how many consecutive frames must match before punching
COOLDOWN_SEC    = 10     # seconds before same person can punch again

# ── Load face database ────────────────────────────────────────────────────────
def load_db():
    if not os.path.exists(ENCODINGS_FILE):
        print(f"[ERROR] No enrolled faces found at:\n        {ENCODINGS_FILE}")
        print("        Run START_ENROLL.bat first to enroll employees.")
        input("\nPress ENTER to exit...")
        sys.exit(1)
    with open(ENCODINGS_FILE, 'r') as f:
        raw = json.load(f)
    db = {}
    for uid, encs in raw.items():
        # Skip non-array entries (opencv placeholder mode)
        valid = [np.array(e) for e in encs if isinstance(e, list)]
        if valid:
            db[uid] = valid
    if not db:
        print("[ERROR] No valid face encodings found.")
        print("        Re-enroll using START_ENROLL.bat")
        input("\nPress ENTER to exit...")
        sys.exit(1)
    print(f"[DB] Loaded {len(db)} enrolled employee(s): {', '.join(db.keys())}")
    return db

# ── Employee names from HTML system ──────────────────────────────────────────
EMPLOYEES_FILE = os.path.join(BASE_DIR, 'employees.json')

def load_emp_names():
    """Returns dict uid → display name from employees.json if available."""
    if os.path.exists(EMPLOYEES_FILE):
        try:
            with open(EMPLOYEES_FILE, 'r') as f:
                emps = json.load(f)
            return {e['uid']: e['name'] for e in emps if 'uid' in e and 'name' in e}
        except Exception:
            pass
    return {}

# ── CSV Attendance Log ────────────────────────────────────────────────────────
def ensure_log():
    if not os.path.exists(LOG_FILE):
        with open(LOG_FILE, 'w', newline='') as f:
            csv.writer(f).writerow(['Date', 'Time', 'Employee_ID', 'Employee_Name', 'Type', 'Confidence%'])
        print(f"[LOG] Created attendance log: {LOG_FILE}")

PY_SERVER = 'http://localhost:5000'

def record_punch(uid, name, punch_type, confidence):
    now = datetime.now()
    # 1 — Save to CSV (always works, no server needed)
    row = [now.strftime('%Y-%m-%d'), now.strftime('%H:%M:%S'),
           uid, name, punch_type, f"{confidence:.1f}"]
    with open(LOG_FILE, 'a', newline='') as f:
        csv.writer(f).writerow(row)
    print(f"[PUNCH] {punch_type} — {name} ({uid})  {now.strftime('%H:%M:%S')}  conf={confidence:.1f}%")

    # 2 — POST to face_server.py so HTML reports can show it
    try:
        import urllib.request, urllib.error
        payload = json.dumps({
            'uid':        uid,
            'name':       name,
            'type':       punch_type.lower(),
            'confidence': confidence,
            'timestamp':  now.isoformat()
        }).encode('utf-8')
        req = urllib.request.Request(
            f'{PY_SERVER}/punch',
            data=payload,
            headers={'Content-Type': 'application/json'},
            method='POST'
        )
        urllib.request.urlopen(req, timeout=2)
        print(f"[SYNC]  Record sent to HTML system ✓")
    except Exception as e:
        print(f"[SYNC]  Server offline — record saved to CSV only ({e})")

def get_last_punch(uid):
    """Return last punch type for this uid today ('IN'/'OUT' or None)."""
    if not os.path.exists(LOG_FILE):
        return None
    today = datetime.now().strftime('%Y-%m-%d')
    last = None
    with open(LOG_FILE, 'r') as f:
        for row in csv.reader(f):
            if len(row) >= 4 and row[0] == today and row[2] == uid:
                last = row[3]
    return last

# ── Camera ────────────────────────────────────────────────────────────────────
def open_camera():
    backends = [(cv2.CAP_DSHOW, "DirectShow"), (cv2.CAP_MSMF, "MSMF"), (None, "Default")]
    for backend, bname in backends:
        for idx in range(3):
            print(f"[CAM] Trying index {idx} ({bname})...")
            try:
                cap = cv2.VideoCapture(idx, backend) if backend else cv2.VideoCapture(idx)
                if not cap.isOpened():
                    cap.release(); continue
                cap.set(cv2.CAP_PROP_FRAME_WIDTH,  640)
                cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
                cap.set(cv2.CAP_PROP_FPS, 30)
                time.sleep(0.8)
                for _ in range(20):
                    ret, frame = cap.read()
                    if ret and frame is not None and frame.mean() > 1.0:
                        print(f"[CAM] ✓ Camera {idx} ({bname}) working!")
                        cap.set(cv2.CAP_PROP_FRAME_WIDTH,  1280)
                        cap.set(cv2.CAP_PROP_FRAME_HEIGHT,  720)
                        time.sleep(0.3)
                        return cap, idx
                    time.sleep(0.05)
                cap.release()
            except Exception:
                pass
    return None, -1

# ── Drawing helpers ───────────────────────────────────────────────────────────
def draw_face_box(frame, top, right, bottom, left, color, label, sub=""):
    cs = max(12, int((right - left) * 0.18))
    corners = [
        [(left, top+cs),(left, top),(left+cs, top)],
        [(right-cs, top),(right, top),(right, top+cs)],
        [(left, bottom-cs),(left, bottom),(left+cs, bottom)],
        [(right-cs, bottom),(right, bottom),(right, bottom-cs)],
    ]
    for pts in corners:
        for i in range(len(pts)-1):
            cv2.line(frame, pts[i], pts[i+1], color, 2, cv2.LINE_AA)
    # Label
    (tw, th), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.6, 1)
    cv2.rectangle(frame, (left, top - th - 14), (left + tw + 10, top), color, -1)
    cv2.putText(frame, label, (left+5, top-6),
                cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0,0,0), 1, cv2.LINE_AA)
    if sub:
        cv2.putText(frame, sub, (left+4, bottom+18),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.42, color, 1, cv2.LINE_AA)

def draw_status_bar(frame, text, color, sub=""):
    h, w = frame.shape[:2]
    overlay = frame.copy()
    cv2.rectangle(overlay, (0, h-80), (w, h), (10, 20, 40), -1)
    cv2.addWeighted(overlay, 0.85, frame, 0.15, 0, frame)
    cv2.putText(frame, text, (14, h-48),
                cv2.FONT_HERSHEY_SIMPLEX, 0.7, color, 2, cv2.LINE_AA)
    if sub:
        cv2.putText(frame, sub, (14, h-18),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.45, (148,163,184), 1, cv2.LINE_AA)
    # Clock
    now_str = datetime.now().strftime('%H:%M:%S   %d %b %Y')
    (cw, _), _ = cv2.getTextSize(now_str, cv2.FONT_HERSHEY_SIMPLEX, 0.45, 1)
    cv2.putText(frame, now_str, (w - cw - 12, h-18),
                cv2.FONT_HERSHEY_SIMPLEX, 0.45, (100,160,220), 1, cv2.LINE_AA)
    # Badge
    cv2.putText(frame, "BDI · FACE ATTENDANCE", (14, h-68),
                cv2.FONT_HERSHEY_SIMPLEX, 0.38, (232,119,34), 1, cv2.LINE_AA)

def draw_punch_banner(frame, uid, name, punch_type, conf):
    h, w = frame.shape[:2]
    color = (34,197,94) if punch_type == "IN" else (60,120,255)
    overlay = frame.copy()
    cv2.rectangle(overlay, (0, h//2-80), (w, h//2+80), (10,20,40), -1)
    cv2.addWeighted(overlay, 0.88, frame, 0.12, 0, frame)
    cv2.rectangle(frame, (0, h//2-80), (w, h//2+80), color, 3)
    msg = f"PUNCHED {punch_type}"
    (mw,_), _ = cv2.getTextSize(msg, cv2.FONT_HERSHEY_SIMPLEX, 1.5, 3)
    cv2.putText(frame, msg, ((w-mw)//2, h//2-20),
                cv2.FONT_HERSHEY_SIMPLEX, 1.5, color, 3, cv2.LINE_AA)
    # Show real name if available, else UID
    display = name if name else uid
    (nw,_), _ = cv2.getTextSize(display, cv2.FONT_HERSHEY_SIMPLEX, 0.75, 2)
    cv2.putText(frame, display, ((w-nw)//2, h//2+28),
                cv2.FONT_HERSHEY_SIMPLEX, 0.75, (255,255,255), 2, cv2.LINE_AA)
    sub = f"ID: {uid}   |   {conf:.0f}% match   |   {datetime.now().strftime('%H:%M:%S')}"
    (sw,_), _ = cv2.getTextSize(sub, cv2.FONT_HERSHEY_SIMPLEX, 0.5, 1)
    cv2.putText(frame, sub, ((w-sw)//2, h//2+60),
                cv2.FONT_HERSHEY_SIMPLEX, 0.5, (200,220,200), 1, cv2.LINE_AA)


# ── Main scanner loop ─────────────────────────────────────────────────────────
def run_scanner(cap, face_db):
    hit_counts   = {}   # uid → consecutive match count
    last_punch   = {}   # uid → timestamp of last punch
    banner_until = 0    # show punch banner until this time
    banner_data  = {}   # uid, name, type, conf
    emp_names    = load_emp_names()   # uid → real name from HTML system

    if emp_names:
        print(f"[INFO] Loaded {len(emp_names)} employee name(s) from HTML system")
    else:
        print("[INFO] employees.json not found — showing UIDs only")
        print("       Open admin.html → save any employee → names will appear here")

    print("\n[SCAN] Scanner running. Stand in front of the camera.")
    print("       Press Q or ESC to quit.\n")

    while True:
        ret, raw = cap.read()
        if not ret or raw is None or raw.mean() < 2.0:
            time.sleep(0.05)
            continue

        frame = cv2.flip(raw, 1)
        h, w  = frame.shape[:2]
        rgb   = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)

        # Shrink for faster detection (process at half size)
        small = cv2.resize(rgb, (0,0), fx=0.5, fy=0.5)
        locations = face_recognition.face_locations(small, model='hog')
        # Scale locations back to full size
        locations = [(t*2, r*2, b*2, l*2) for t,r,b,l in locations]

        if locations:
            encodings = face_recognition.face_encodings(rgb, locations, num_jitters=1, model='large')
        else:
            encodings = []

        recognized_uids = set()

        for loc, enc in zip(locations, encodings):
            top, right, bottom, left = loc

            # Compare against all enrolled faces
            best_uid  = None
            best_dist = float('inf')
            for uid, stored_encs in face_db.items():
                dists    = face_recognition.face_distance(stored_encs, enc)
                min_dist = float(np.min(dists))
                if min_dist < best_dist:
                    best_dist = min_dist
                    best_uid  = uid

            if best_dist <= THRESHOLD:
                conf      = (1 - best_dist) * 100
                disp_name = emp_names.get(best_uid, best_uid)   # real name or UID fallback
                recognized_uids.add(best_uid)
                hit_counts[best_uid] = hit_counts.get(best_uid, 0) + 1

                # Check cooldown
                now_ts  = time.time()
                on_cool = best_uid in last_punch and (now_ts - last_punch[best_uid]) < COOLDOWN_SEC

                if on_cool:
                    remaining = int(COOLDOWN_SEC - (now_ts - last_punch[best_uid]))
                    color = (100, 200, 255)
                    draw_face_box(frame, top, right, bottom, left, color,
                                  disp_name, f"Please wait {remaining}s")
                elif hit_counts[best_uid] >= CONFIRM_FRAMES:
                    # ── PUNCH! ────────────────────────────────────────────────
                    last_type  = get_last_punch(best_uid)
                    punch_type = "OUT" if last_type == "IN" else "IN"
                    record_punch(best_uid, disp_name, punch_type, conf)
                    last_punch[best_uid]  = now_ts
                    hit_counts[best_uid]  = 0
                    banner_until = now_ts + 3.5
                    banner_data  = {'uid': best_uid, 'name': disp_name,
                                    'type': punch_type, 'conf': conf}
                    color = (34,197,94) if punch_type == "IN" else (60,120,255)
                    draw_face_box(frame, top, right, bottom, left, color,
                                  disp_name, f"PUNCH {punch_type}  {conf:.0f}%")
                else:
                    # Building confirmation
                    pct   = int((hit_counts[best_uid] / CONFIRM_FRAMES) * 100)
                    color = (60, 200, 120)
                    draw_face_box(frame, top, right, bottom, left, color,
                                  disp_name, f"Confirming… {pct}%")
            else:
                # Unknown face
                draw_face_box(frame, top, right, bottom, left,
                              (60, 60, 200), "Unknown")

        # Reset hit counts for faces no longer visible
        for uid in list(hit_counts.keys()):
            if uid not in recognized_uids:
                hit_counts[uid] = 0

        # ── Status bar ────────────────────────────────────────────────────────
        if locations:
            n = len(locations)
            draw_status_bar(frame, f"{n} face{'s' if n>1 else ''} detected",
                            (34,197,94), "SPACE = manual punch  |  Q = quit")
        else:
            draw_status_bar(frame, "No face detected — stand in front of camera",
                            (148,163,184), "Enrolled: " + ", ".join(face_db.keys()))

        # ── Punch banner overlay ───────────────────────────────────────────────
        if time.time() < banner_until and banner_data:
            draw_punch_banner(frame, banner_data['uid'], banner_data.get('name',''),
                              banner_data['type'], banner_data['conf'])

        cv2.imshow("BDI Face Attendance", frame)
        key = cv2.waitKey(1) & 0xFF
        if key in (27, ord('q'), ord('Q')):
            break


# ── Entry point ───────────────────────────────────────────────────────────────
def main():
    face_db = load_db()
    ensure_log()

    print(f"\n[LOG] Attendance will be saved to:\n      {LOG_FILE}")
    print(f"[CFG] Threshold={THRESHOLD}  Confirm={CONFIRM_FRAMES} frames  Cooldown={COOLDOWN_SEC}s\n")

    print("[CAM] Searching for camera...")
    cap, idx = open_camera()
    if cap is None:
        print("\n[ERROR] No camera found!")
        print("        - Check webcam is connected")
        print("        - Close any other app using the camera")
        input("\nPress ENTER to exit...")
        sys.exit(1)

    cv2.namedWindow("BDI Face Attendance", cv2.WINDOW_NORMAL)
    cv2.resizeWindow("BDI Face Attendance", 960, 620)

    # Loading screen while warming up
    print("[CAM] Warming up...")
    for i in range(20):
        cap.read()
        loading = np.zeros((480, 640, 3), dtype=np.uint8)
        bar_w = int((i / 20) * 400)
        cv2.rectangle(loading, (120,255), (520,275), (30,50,70), -1)
        cv2.rectangle(loading, (120,255), (120+bar_w,275), (34,197,94), -1)
        cv2.putText(loading, "BDI Face Attendance", (145,215),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.9, (232,119,34), 2, cv2.LINE_AA)
        cv2.putText(loading, "Starting camera...", (190,305),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, (148,163,184), 1, cv2.LINE_AA)
        enrolled_str = f"Enrolled: {', '.join(face_db.keys())}"
        cv2.putText(loading, enrolled_str, (120,350),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.45, (80,160,120), 1, cv2.LINE_AA)
        cv2.imshow("BDI Face Attendance", loading)
        cv2.waitKey(60)

    try:
        run_scanner(cap, face_db)
    finally:
        cap.release()
        cv2.destroyAllWindows()
        print("\n[EXIT] Session ended.")
        print(f"       Attendance log saved to: {LOG_FILE}")
        input("\nPress ENTER to exit...")


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n[EXIT] Interrupted.")
        cv2.destroyAllWindows()
    except Exception as e:
        print(f"\n[FATAL ERROR] {e}")
        import traceback
        traceback.print_exc()
        input("\nPress ENTER to exit...")
