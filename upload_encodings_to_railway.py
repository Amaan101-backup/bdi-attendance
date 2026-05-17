"""
BDI Attendance — Upload Local Face Encodings to Railway
=========================================================
Run this ONCE to push your locally enrolled faces to the cloud server.
After this, all devices (web/phone/tablet) can recognize enrolled faces.

Usage:
  python upload_encodings_to_railway.py
"""

import json
import os
import urllib.request
import urllib.error

# ── Config ────────────────────────────────────────────────────────────────────
RAILWAY_URL    = 'https://bdi-attendance-production.up.railway.app'
IMPORT_SECRET  = 'bdi-import-2024'
ENCODINGS_FILE = os.path.join(os.path.dirname(__file__), 'face_encodings.json')
# ─────────────────────────────────────────────────────────────────────────────

def main():
    print("=" * 55)
    print("  BDI — Upload Face Encodings to Railway Cloud")
    print("=" * 55)

    # Load local encodings
    if not os.path.exists(ENCODINGS_FILE):
        print(f"\n❌  ERROR: {ENCODINGS_FILE} not found!")
        print("   Please enroll faces first using face_enroll_python.py")
        input("\nPress Enter to exit...")
        return

    with open(ENCODINGS_FILE, 'r') as f:
        encodings = json.load(f)

    if not encodings:
        print("\n❌  No face encodings found in file. Enroll some faces first.")
        input("\nPress Enter to exit...")
        return

    print(f"\n  Found {len(encodings)} enrolled employee(s) locally:")
    for uid in encodings:
        samples = len(encodings[uid])
        print(f"    • UID {uid}  ({samples} sample{'s' if samples != 1 else ''})")

    print(f"\n  Uploading to: {RAILWAY_URL}")
    print("  Please wait...\n")

    # POST to Railway
    payload = json.dumps({
        'secret': IMPORT_SECRET,
        'encodings': encodings
    }).encode('utf-8')

    req = urllib.request.Request(
        f"{RAILWAY_URL}/import-encodings",
        data=payload,
        headers={'Content-Type': 'application/json'},
        method='POST'
    )

    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            result = json.loads(resp.read())

        if result.get('ok'):
            print(f"  ✅  SUCCESS!")
            print(f"      Imported : {result.get('imported', 0)} employees")
            print(f"      New      : {result.get('new', 0)}")
            print(f"      Total on server: {result.get('total', 0)}")
            print(f"\n  Your web app at Netlify can now recognize faces!")
            print(f"  Share this URL with supervisors:")
            print(f"  → https://your-netlify-site.netlify.app/attendance.html")
        else:
            print(f"  ❌  Server error: {result.get('error', 'Unknown')}")

    except urllib.error.URLError as e:
        print(f"  ❌  Connection error: {e}")
        print(f"\n  Make sure Railway is running:")
        print(f"  → {RAILWAY_URL}/status")

    except Exception as e:
        print(f"  ❌  Unexpected error: {e}")

    print()
    input("Press Enter to exit...")

if __name__ == '__main__':
    main()
