"""
BDI Attendance — Python Face Enrollment Station
================================================
Live camera with 68-point facial landmark mapping.
Press SPACE to capture each sample (need 3).
Saves face ID to face_encodings.json for the server.

INSTALL (once):
  pip install opencv-python face_recognition numpy

RUN:
  python face_enroll_python.py
"""

import sys
import os

# ── Dependency check ─────────────────────────────────────────────────────────
print("=" * 55)
print("  BDI Face Enrollment Station")
print("=" * 55)

# Check OpenCV
try:
    import cv2
    print(f"  [OK] OpenCV {cv2.__version__}")
except ImportError:
    print("  [ERROR] OpenCV not installed.")
    print("  Run:  pip install opencv-python")
    input("\n  Press ENTER to exit...")
    sys.exit(1)

# Check face_recognition (optional — fallback to OpenCV Haar if missing)
FACE_REC_AVAILABLE = False
try:
    import face_recognition
    FACE_REC_AVAILABLE = True
    print("  [OK] face_recognition (dlib)")
except ImportError:
    print("  [WARN] face_recognition not installed.")
    print("         Using OpenCV Haar cascade (less accurate).")
    print("         For best results: pip install face_recognition")

import numpy as np
import json
import time

print("=" * 55)

# ── Storage ───────────────────────────────────────────────────────────────────
ENCODINGS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'face_encodings.json')
SAMPLES_NEEDED = 3

face_db = {}

def load_db():
    global face_db
    if os.path.exists(ENCODINGS_FILE):
        try:
            with open(ENCODINGS_FILE, 'r') as f:
                raw = json.load(f)
            if FACE_REC_AVAILABLE:
                face_db = {uid: [np.array(e) for e in encs] for uid, encs in raw.items()}
            else:
                face_db = {uid: encs for uid, encs in raw.items()}
            print(f"[DB] Loaded {len(face_db)} enrolled employee(s)")
        except Exception as e:
            print(f"[DB] Load error: {e}")
            face_db = {}

def save_db():
    try:
        if FACE_REC_AVAILABLE:
            raw = {uid: [e.tolist() for e in encs] for uid, encs in face_db.items()}
        else:
            raw = face_db
        with open(ENCODINGS_FILE, 'w') as f:
            json.dump(raw, f, indent=2)
        print(f"[DB] Saved {len(face_db)} employee(s) to {ENCODINGS_FILE}")
    except Exception as e:
        print(f"[DB] Save error: {e}")

# ── Camera auto-detect ────────────────────────────────────────────────────────
def _try_cap(idx, backend=None):
    """Open camera index with optional backend, wait for a real non-black frame."""
    try:
        cap = cv2.VideoCapture(idx, backend) if backend is not None else cv2.VideoCapture(idx)
        if not cap.isOpened():
            cap.release()
            return None

        # Set resolution BEFORE reading frames
        cap.set(cv2.CAP_PROP_FRAME_WIDTH,  640)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
        cap.set(cv2.CAP_PROP_FPS, 30)

        # Give the camera time to start
        time.sleep(0.8)

        # Try up to 30 reads to get a real (non-black) frame
        for _ in range(30):
            ret, frame = cap.read()
            if ret and frame is not None and frame.size > 0:
                # Check it's not an all-black frame
                if frame.mean() > 1.0:
                    return cap
            time.sleep(0.05)

        cap.release()
        return None
    except Exception:
        return None

def open_camera():
    """Try multiple backends and indices, return the first working camera."""
    backends = [
        (cv2.CAP_DSHOW,  "DirectShow"),
        (cv2.CAP_MSMF,   "MSMF"),
        (None,            "Default"),
    ]

    for backend, bname in backends:
        for idx in range(3):
            label = f"index {idx} ({bname})"
            print(f"[CAM] Trying {label}...")
            cap = _try_cap(idx, backend)
            if cap is not None:
                print(f"[CAM] ✓ Camera {label} is working!")
                # Bump resolution now that it's alive
                cap.set(cv2.CAP_PROP_FRAME_WIDTH,  1280)
                cap.set(cv2.CAP_PROP_FRAME_HEIGHT,  720)
                time.sleep(0.3)
                return cap, idx

    return None, -1

# ── Haar cascade fallback (when face_recognition not installed) ───────────────
haar_cascade = None
def get_haar():
    global haar_cascade
    if haar_cascade is None:
        haar_path = cv2.data.haarcascades + 'haarcascade_frontalface_default.xml'
        haar_cascade = cv2.CascadeClassifier(haar_path)
    return haar_cascade

def detect_faces_haar(frame):
    """Returns list of (top, right, bottom, left) tuples."""
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    cascade = get_haar()
    faces = cascade.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=5, minSize=(60, 60))
    locations = []
    for (x, y, w, h) in faces:
        locations.append((y, x + w, y + h, x))   # top, right, bottom, left
    return locations


# ── Landmark drawing ──────────────────────────────────────────────────────────
REGION_COLORS = {
    'chin':          (150, 150, 150),
    'left_eyebrow':  (255, 160,  60),
    'right_eyebrow': (255, 160,  60),
    'nose_bridge':   ( 60, 200, 255),
    'nose_tip':      ( 60, 200, 255),
    'left_eye':      ( 80, 220, 130),
    'right_eye':     ( 80, 220, 130),
    'top_lip':       ( 34, 119, 232),
    'bottom_lip':    ( 34, 119, 232),
}
CLOSED_REGIONS = {'left_eye', 'right_eye', 'top_lip', 'bottom_lip', 'chin'}

def draw_face_mesh(frame, landmarks_dict, alpha=0.85):
    overlay = frame.copy()
    for region, color in REGION_COLORS.items():
        pts = landmarks_dict.get(region, [])
        if not pts:
            continue
        pts_arr = np.array([(p[0], p[1]) for p in pts], dtype=np.int32)
        closed  = region in CLOSED_REGIONS
        cv2.polylines(overlay, [pts_arr], closed, color, 1, cv2.LINE_AA)
        for p in pts:
            cv2.circle(overlay, (p[0], p[1]), 2, color, -1, cv2.LINE_AA)
    # Key anchor dots
    for region in ['left_eye', 'right_eye', 'nose_tip', 'top_lip']:
        pts = landmarks_dict.get(region, [])
        if pts:
            for p in [pts[0], pts[-1]]:
                cv2.circle(overlay, (p[0], p[1]), 4, (255,255,255), -1, cv2.LINE_AA)
                cv2.circle(overlay, (p[0], p[1]), 4, REGION_COLORS.get(region,(255,255,255)), 1, cv2.LINE_AA)
    cv2.addWeighted(overlay, alpha, frame, 1 - alpha, 0, frame)

def draw_face_box(frame, top, right, bottom, left, color, label, conf=None):
    cs = max(10, int((right - left) * 0.15))
    corners = [
        [(left, top+cs), (left, top), (left+cs, top)],
        [(right-cs, top), (right, top), (right, top+cs)],
        [(left, bottom-cs), (left, bottom), (left+cs, bottom)],
        [(right-cs, bottom), (right, bottom), (right, bottom-cs)],
    ]
    for pts in corners:
        for i in range(len(pts)-1):
            cv2.line(frame, pts[i], pts[i+1], color, 2, cv2.LINE_AA)
    text = label if conf is None else f"{label} {conf}%"
    (tw, th), _ = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, 0.5, 1)
    cv2.rectangle(frame, (left, top - th - 10), (left + tw + 8, top), color, -1)
    cv2.putText(frame, text, (left + 4, top - 6),
                cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0,0,0), 1, cv2.LINE_AA)

def draw_ui_overlay(frame, state):
    h, w = frame.shape[:2]
    panel_h = 120
    overlay = frame.copy()
    cv2.rectangle(overlay, (0, h - panel_h), (w, h), (10, 20, 40), -1)
    cv2.addWeighted(overlay, 0.82, frame, 0.18, 0, frame)

    # Employee info
    cv2.putText(frame, f"Employee: {state['name']}  ({state['uid']})",
                (14, h - panel_h + 22), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (232, 119, 34), 1, cv2.LINE_AA)

    # Sample progress dots
    bar_y, bar_x, dot_size = h - panel_h + 42, 14, 22
    for i in range(SAMPLES_NEEDED):
        done  = i < state['samples']
        color = (34, 197, 94) if done else (50, 70, 90)
        cx = bar_x + i * (dot_size + 10) + dot_size // 2
        cy = bar_y + dot_size // 2
        cv2.circle(frame, (cx, cy), dot_size // 2, color, -1, cv2.LINE_AA)
        num = str(i + 1)
        (nw, nh), _ = cv2.getTextSize(num, cv2.FONT_HERSHEY_SIMPLEX, 0.45, 1)
        cv2.putText(frame, num, (cx - nw//2, cy + nh//2),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.45, (255,255,255), 1, cv2.LINE_AA)

    px = bar_x + SAMPLES_NEEDED * (dot_size + 10) + 10
    cv2.putText(frame, f"{state['samples']}/{SAMPLES_NEEDED} samples",
                (px, bar_y + 16), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (148,163,184), 1, cv2.LINE_AA)

    # Status
    st_color = (34, 197, 94) if state['status_ok'] else (60,160,245)
    cv2.putText(frame, state['status'], (14, h - panel_h + 80),
                cv2.FONT_HERSHEY_SIMPLEX, 0.52, st_color, 1, cv2.LINE_AA)

    # Controls
    ctrl = "SPACE=Capture  N=Next employee  ESC=Quit"
    (cw, ch), _ = cv2.getTextSize(ctrl, cv2.FONT_HERSHEY_SIMPLEX, 0.38, 1)
    cv2.putText(frame, ctrl, (w - cw - 10, h - 8),
                cv2.FONT_HERSHEY_SIMPLEX, 0.38, (71,85,105), 1, cv2.LINE_AA)

    # Mode badge
    mode = "BDI · PYTHON AI" if FACE_REC_AVAILABLE else "BDI · OPENCV MODE"
    cv2.putText(frame, mode, (w//2 - 80, h - panel_h + 22),
                cv2.FONT_HERSHEY_SIMPLEX, 0.42, (232,119,34), 1, cv2.LINE_AA)

def draw_flash(frame, color_bgr, alpha=0.45):
    overlay = np.full_like(frame, color_bgr)
    cv2.addWeighted(overlay, alpha, frame, 1 - alpha, 0, frame)


# ── Employee list from HTML system ────────────────────────────────────────────
EMPLOYEES_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'employees.json')

def load_employees():
    if os.path.exists(EMPLOYEES_FILE):
        try:
            with open(EMPLOYEES_FILE, 'r') as f:
                return json.load(f)
        except Exception:
            pass
    return []

# ── Enrollment loop ───────────────────────────────────────────────────────────
def get_employee_info():
    print("\n" + "─"*50)
    print("  EMPLOYEE FACE ENROLLMENT")
    print("─"*50)

    employees = load_employees()

    if employees:
        print(f"\n  Found {len(employees)} employee(s) from HTML system:\n")
        print(f"  {'#':<4} {'Name':<25} {'ID':<12} {'Dept':<15} {'Face Enrolled'}")
        print("  " + "─"*70)
        for i, e in enumerate(employees, 1):
            enrolled = "✓ YES" if e.get('uid') in face_db else "  no"
            dept = e.get('dept','')[:14]
            print(f"  {i:<4} {e['name']:<25} {e['uid']:<12} {dept:<15} {enrolled}")
        print()
        choice = input("  Enter number to select, or 0 to enter manually: ").strip()

        if choice.isdigit() and 1 <= int(choice) <= len(employees):
            emp = employees[int(choice)-1]
            name = emp['name']
            uid  = emp['uid']
            print(f"\n  Selected: {name}  [{uid}]")
            if uid in face_db:
                print(f"  ⚡ Already enrolled ({len(face_db[uid])} samples) — will re-enroll")
            print("  Camera will open. Press SPACE to capture, ESC to quit.\n")
            return name, uid
        elif choice == '0':
            pass   # fall through to manual entry
        else:
            print("  Invalid choice — entering manually.")

    # Manual entry
    name = input("  Employee Name  : ").strip()
    if not name:
        return None, None
    uid = input("  Employee ID    : ").strip()
    if not uid:
        uid = name.lower().replace(' ', '_')
    print(f"\n  Enrolling: {name}  [{uid}]")
    if uid in face_db:
        print(f"  Already enrolled ({len(face_db[uid])} samples) — will re-enroll")
    print("  Camera will open. Press SPACE to capture, ESC to quit.\n")
    return name, uid


def enroll_employee(cap, name, uid):
    samples      = []
    flash_frames = 0
    flash_color  = (0, 255, 0)
    last_status  = "Position face in frame, then press SPACE"
    last_ok      = False

    blank_count = 0   # count consecutive blank frames

    while True:
        ret, raw_frame = cap.read()
        if not ret or raw_frame is None:
            blank_count += 1
            # Show a "loading" placeholder so window doesn't freeze
            placeholder = np.zeros((480, 640, 3), dtype=np.uint8)
            msg = "Camera loading..." if blank_count < 20 else "Camera error — check connection"
            cv2.putText(placeholder, msg, (80, 240),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.9, (100, 180, 255), 2, cv2.LINE_AA)
            cv2.imshow("BDI Face Enrollment", placeholder)
            cv2.waitKey(50)
            continue

        # Black frame check — camera sometimes sends black while warming up
        if raw_frame.mean() < 2.0:
            blank_count += 1
            placeholder = np.zeros((480, 640, 3), dtype=np.uint8)
            cv2.putText(placeholder, "Warming up camera...", (100, 240),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.9, (100, 180, 255), 2, cv2.LINE_AA)
            cv2.imshow("BDI Face Enrollment", placeholder)
            cv2.waitKey(50)
            continue

        blank_count = 0
        frame = cv2.flip(raw_frame, 1)
        h, w  = frame.shape[:2]

        # ── Detect faces ──────────────────────────────────────────────────────
        if FACE_REC_AVAILABLE:
            rgb       = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            locations = face_recognition.face_locations(rgb, model='hog')
            lm_list   = face_recognition.face_landmarks(rgb, locations) if locations else []
        else:
            locations = detect_faces_haar(frame)
            lm_list   = [{}] * len(locations)   # no landmarks without face_recognition

        face_found = bool(locations)

        # ── Draw each face ────────────────────────────────────────────────────
        for loc, lm in zip(locations, lm_list):
            top, right, bottom, left = loc
            face_area  = (right - left) * (bottom - top)
            ratio      = face_area / (w * h)

            if ratio < 0.04:
                box_color   = (0, 180, 255)
                label       = "Move closer"
                last_status = "Face too small — step closer to the camera"
                last_ok     = False
            else:
                box_color   = (80, 220, 130)
                label       = f"Ready [{len(samples)}/{SAMPLES_NEEDED}]"
                last_status = f"Face OK — press SPACE to capture sample {len(samples)+1}/{SAMPLES_NEEDED}"
                last_ok     = True

            if lm:
                draw_face_mesh(frame, lm)
            draw_face_box(frame, top, right, bottom, left, box_color, label)

        if not face_found:
            last_status = "No face detected — face camera, improve lighting"
            last_ok     = False

        # ── Flash ─────────────────────────────────────────────────────────────
        if flash_frames > 0:
            draw_flash(frame, flash_color)
            flash_frames -= 1

        # ── UI panel ──────────────────────────────────────────────────────────
        already = len(face_db.get(uid, []))
        draw_ui_overlay(frame, {
            'name': name, 'uid': uid,
            'samples': len(samples),
            'status': last_status, 'status_ok': last_ok,
            'already': already if uid in face_db else None,
        })

        cv2.imshow("BDI Face Enrollment", frame)
        key = cv2.waitKey(1) & 0xFF

        # ── SPACE — capture ───────────────────────────────────────────────────
        if key == ord(' '):
            if not face_found:
                last_status = "No face — position face and try again"
                flash_color = (0, 0, 200)
                flash_frames = 8
                continue

            if len(locations) > 1:
                last_status = "Multiple faces — only ONE person at a time"
                flash_color = (0, 100, 255)
                flash_frames = 8
                continue

            if FACE_REC_AVAILABLE:
                print(f"[CAPTURE] Extracting encoding for sample {len(samples)+1}…")
                rgb  = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                encs = face_recognition.face_encodings(rgb, locations, num_jitters=3, model='large')
                if encs:
                    samples.append(encs[0])
                else:
                    last_status = "Encoding failed — keep still and try again"
                    flash_color = (0, 0, 200)
                    flash_frames = 8
                    continue
            else:
                # OpenCV mode: store the face crop as a placeholder
                top, right, bottom, left = locations[0]
                crop = frame[top:bottom, left:right]
                samples.append(crop)

            print(f"[CAPTURE] Sample {len(samples)}/{SAMPLES_NEEDED} ✓")
            flash_color  = (0, 255, 80)
            flash_frames = 12
            last_status  = f"✓ Sample {len(samples)}/{SAMPLES_NEEDED} captured!"
            last_ok      = True

            # ── All samples done ──────────────────────────────────────────────
            if len(samples) >= SAMPLES_NEEDED:
                if FACE_REC_AVAILABLE:
                    face_db[uid] = samples
                else:
                    # Store placeholder info (can't do real recognition without face_recognition)
                    face_db[uid] = [f"opencv_sample_{i}" for i in range(SAMPLES_NEEDED)]
                save_db()
                print(f"\n[✓] {name} ({uid}) ENROLLED with {SAMPLES_NEEDED} samples")

                # Success animation 2s
                for _ in range(30):
                    ret2, raw2 = cap.read()
                    if ret2:
                        f2 = cv2.flip(raw2, 1)
                        draw_flash(f2, (0, 200, 80), 0.55)
                        fh2, fw2 = f2.shape[:2]
                        msg = f"ENROLLED: {name}"
                        (mw, mh), _ = cv2.getTextSize(msg, cv2.FONT_HERSHEY_SIMPLEX, 1.2, 2)
                        cv2.putText(f2, msg, ((fw2-mw)//2, fh2//2),
                                    cv2.FONT_HERSHEY_SIMPLEX, 1.2, (255,255,255), 2, cv2.LINE_AA)
                        cv2.putText(f2, f"{SAMPLES_NEEDED} samples stored",
                                    ((fw2-200)//2, fh2//2+44),
                                    cv2.FONT_HERSHEY_SIMPLEX, 0.65, (200,255,200), 1, cv2.LINE_AA)
                        cv2.imshow("BDI Face Enrollment", f2)
                        cv2.waitKey(60)
                return True

        elif key in (ord('n'), ord('N')):
            return False
        elif key == 27:
            return None

    return False


# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    load_db()

    print(f"\n  Currently enrolled: {len(face_db)} employee(s)")
    if not FACE_REC_AVAILABLE:
        print("\n  [!] Running in OpenCV-only mode.")
        print("      Faces will be captured but NOT encoded for recognition.")
        print("      Install face_recognition for full functionality.\n")

    # Open camera
    print("\n[CAM] Searching for camera...")
    cap, idx = open_camera()

    if cap is None:
        print("\n[ERROR] No camera found!")
        print("        - Make sure your webcam is connected")
        print("        - Make sure no other app is using the camera")
        print("        - Try running as Administrator")
        input("\nPress ENTER to exit...")
        sys.exit(1)

    actual_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    actual_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    print(f"[CAM] Using camera {idx} at {actual_w}x{actual_h}\n")

    cv2.namedWindow("BDI Face Enrollment", cv2.WINDOW_NORMAL)
    cv2.resizeWindow("BDI Face Enrollment", 960, 620)

    # Show loading screen while camera warms up
    print("[CAM] Warming up camera (this takes 1-2 seconds)...")
    for i in range(25):
        ret, frame = cap.read()
        loading = np.zeros((480, 640, 3), dtype=np.uint8)
        bar_w = int((i / 25) * 400)
        cv2.rectangle(loading, (120, 260), (520, 280), (40, 60, 80), -1)
        cv2.rectangle(loading, (120, 260), (120 + bar_w, 280), (34, 197, 94), -1)
        cv2.putText(loading, "BDI Face Enrollment", (155, 220),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.9, (232, 119, 34), 2, cv2.LINE_AA)
        cv2.putText(loading, "Starting camera...", (190, 310),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, (148, 163, 184), 1, cv2.LINE_AA)
        cv2.imshow("BDI Face Enrollment", loading)
        cv2.waitKey(60)
    print("[CAM] Ready!\n")

    while True:
        name, uid = get_employee_info()
        if not name:
            print("\n[EXIT] No name entered — exiting.")
            break

        result = enroll_employee(cap, name, uid)

        if result is None:
            print("\n[EXIT] Quit by user.")
            break
        elif result:
            ans = input("\n  Enroll another employee? (Y/N): ").strip().lower()
            if ans != 'y':
                break
        else:
            ans = input("\n  Try again or different employee? (Y/N): ").strip().lower()
            if ans != 'y':
                break

    cap.release()
    cv2.destroyAllWindows()

    print("\n" + "=" * 55)
    print("  Session complete.")
    print(f"  Total enrolled: {len(face_db)} employee(s)")
    for uid, encs in face_db.items():
        count = len(encs) if isinstance(encs, list) else 0
        print(f"    • {uid}: {count} sample(s)")
    print("=" * 55)
    input("\nPress ENTER to exit...")


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n[EXIT] Interrupted by user.")
        cv2.destroyAllWindows()
    except Exception as e:
        print(f"\n[FATAL ERROR] {e}")
        import traceback
        traceback.print_exc()
        input("\nPress ENTER to exit...")
