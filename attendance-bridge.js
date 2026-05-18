/**
 * ══════════════════════════════════════════════════════════════════
 *  BDI Attendance Bridge  —  v1.0
 *  Connects your HR software (pro-erp-system.pages.dev)
 *  to the BDI Attendance backend (Railway)
 *
 *  USAGE — add to any HR page:
 *    <script src="/attendance-bridge.js"></script>
 *
 *  Then call AttendanceBridge methods from your HR JS.
 * ══════════════════════════════════════════════════════════════════
 */

const AttendanceBridge = (() => {

  /* ── Config ───────────────────────────────────────────────────── */
  const BASE = 'https://bdi-attendance-production-d7e9.up.railway.app';
  const ADMIN_URL = BASE + '/admin';   // direct link to admin portal

  /* ── Internal fetch helper ───────────────────────────────────── */
  async function _api(path, method = 'GET', body = null) {
    const opts = {
      method,
      headers: { 'Content-Type': 'application/json' },
    };
    if (body) opts.body = JSON.stringify(body);
    const res = await fetch(BASE + path, opts);
    if (!res.ok) throw new Error(`HTTP ${res.status} on ${path}`);
    return res.json();
  }

  /* ══════════════════════════════════════════════════════════════
     1. SERVER STATUS
     Check if the attendance server is online.
     Returns: { ok, enrolled, face_recognition }
  ══════════════════════════════════════════════════════════════ */
  async function getStatus() {
    return _api('/status');
  }

  /* ══════════════════════════════════════════════════════════════
     2. SYNC EMPLOYEES  (HR → Attendance)
     Push your HR employee list to the attendance system.
     Each employee must have at least: { uid, name }
     Optional: empId, dept, role, phone, sites[]

     Example:
       AttendanceBridge.syncEmployees([
         { uid: 'EMP001', name: 'Ahmed Hassan', dept: 'Engineering', phone: '+971501234567' },
         { uid: 'EMP002', name: 'Sara Ali', dept: 'Operations' }
       ]);
  ══════════════════════════════════════════════════════════════ */
  async function syncEmployees(employees) {
    if (!Array.isArray(employees) || employees.length === 0)
      throw new Error('employees must be a non-empty array');

    // Normalise field names — accepts both HR and attendance field formats
    const normalised = employees.map(e => ({
      uid:    e.uid    || e.id     || e.employeeId || String(Math.random()),
      name:   e.name   || e.fullName || e.employeeName || 'Unknown',
      empId:  e.empId  || e.id     || e.employeeCode || '',
      dept:   e.dept   || e.department || e.div || '',
      role:   e.role   || e.jobTitle   || e.position  || '',
      phone:  e.phone  || e.mobile     || e.contact   || '',
      sites:  e.sites  || e.siteIds    || [],
    }));

    const result = await _api('/employees', 'POST', { employees: normalised });
    console.log(`[AttendanceBridge] Synced ${normalised.length} employees →`, result);
    return result;
  }

  /* ══════════════════════════════════════════════════════════════
     3. GET EMPLOYEES  (Attendance → HR)
     Fetch all employees stored in the attendance system.
     Returns: { ok, employees: [...] }
  ══════════════════════════════════════════════════════════════ */
  async function getEmployees() {
    return _api('/employees');
  }

  /* ══════════════════════════════════════════════════════════════
     4. GET TODAY'S PUNCHES  (Attendance → HR)
     Returns all check-in / check-out records for today.
     Optional siteId filter.

     Returns array of records:
       { empUid, empName, type ('in'/'out'), time, siteId, siteName,
         supervisorName, lat, lng, source }
  ══════════════════════════════════════════════════════════════ */
  async function getTodayPunches(siteId = '') {
    const today = new Date().toISOString().slice(0, 10);
    const d = await _api(`/app/punches?date=${today}`);
    let records = d.records || [];
    if (siteId) records = records.filter(r => r.siteId === siteId);
    return records;
  }

  /* ══════════════════════════════════════════════════════════════
     5. GET PUNCHES BY DATE  (Attendance → HR)
     date: 'YYYY-MM-DD'
     Returns array of punch records (same format as above).
  ══════════════════════════════════════════════════════════════ */
  async function getPunchesByDate(date, siteId = '') {
    const d = await _api(`/app/punches?date=${date}`);
    let records = d.records || [];
    if (siteId) records = records.filter(r => r.siteId === siteId);
    return records;
  }

  /* ══════════════════════════════════════════════════════════════
     6. GET MAN-HOURS  (Attendance → HR)
     Optional date filter ('YYYY-MM-DD'). Omit for all records.

     Returns array:
       { empUid, empName, totalHours, totalMinutes,
         pairs: [{ in, out, minutes }, ...] }
  ══════════════════════════════════════════════════════════════ */
  async function getManHours(date = '') {
    const path = date ? `/app/manhours?date=${date}` : '/app/manhours';
    const d = await _api(path);
    return d.manhours || [];
  }

  /* ══════════════════════════════════════════════════════════════
     7. GET SITES
     Returns array of sites with location, radius, etc.
  ══════════════════════════════════════════════════════════════ */
  async function getSites() {
    const d = await _api('/data');
    return d.sites || [];
  }

  /* ══════════════════════════════════════════════════════════════
     8. GET ENROLLED FACES
     Returns { uid: sampleCount, ... } for all enrolled employees.
  ══════════════════════════════════════════════════════════════ */
  async function getEnrolledFaces() {
    const d = await _api('/list');
    return d.enrolled || {};
  }

  /* ══════════════════════════════════════════════════════════════
     9. LIVE POLL  (auto-refresh)
     Calls your callback every `intervalMs` ms with today's punches.
     Returns a stop function: const stop = AttendanceBridge.pollPunches(...)
  ══════════════════════════════════════════════════════════════ */
  function pollPunches(callback, intervalMs = 30000, siteId = '') {
    const run = async () => {
      try {
        const records = await getTodayPunches(siteId);
        callback(null, records);
      } catch (err) {
        callback(err, []);
      }
    };
    run();  // run immediately
    const timer = setInterval(run, intervalMs);
    return () => clearInterval(timer);  // returns stop function
  }

  /* ══════════════════════════════════════════════════════════════
     10. RENDER WIDGET
     Injects a ready-made live attendance panel into any element.

     Usage:
       AttendanceBridge.renderWidget('#attendance-panel');
       AttendanceBridge.renderWidget(document.getElementById('my-div'));

     Options:
       { siteId, refreshSeconds, title, showManHours }
  ══════════════════════════════════════════════════════════════ */
  function renderWidget(selector, options = {}) {
    const container = typeof selector === 'string'
      ? document.querySelector(selector)
      : selector;

    if (!container) {
      console.error('[AttendanceBridge] renderWidget: element not found:', selector);
      return;
    }

    const opts = {
      siteId:         options.siteId         || '',
      refreshSeconds: options.refreshSeconds  || 30,
      title:          options.title           || 'BDI Attendance — Live',
      showManHours:   options.showManHours    !== false,
    };

    // Inject CSS once
    if (!document.getElementById('_bdi_bridge_style')) {
      const s = document.createElement('style');
      s.id = '_bdi_bridge_style';
      s.textContent = `
        .bdi-widget{font-family:'Inter',system-ui,sans-serif;border:1px solid #e2e8f0;border-radius:14px;overflow:hidden;background:#fff;box-shadow:0 2px 8px rgba(0,0,0,.06)}
        .bdi-widget-head{background:#0f172a;padding:14px 18px;display:flex;align-items:center;justify-content:space-between}
        .bdi-widget-head h3{color:#fff;font-size:14px;font-weight:700;margin:0}
        .bdi-widget-head a{color:#e87722;font-size:12px;font-weight:600;text-decoration:none}
        .bdi-widget-head a:hover{text-decoration:underline}
        .bdi-widget-meta{display:flex;align-items:center;gap:8px;padding:10px 18px;background:#f8fafc;border-bottom:1px solid #e2e8f0;font-size:12px;color:#64748b}
        .bdi-dot{width:7px;height:7px;border-radius:50%;background:#16a34a;flex-shrink:0}
        .bdi-dot.off{background:#ef4444}
        .bdi-widget-body{max-height:380px;overflow-y:auto}
        .bdi-table{width:100%;border-collapse:collapse;font-size:13px}
        .bdi-table th{padding:9px 14px;text-align:left;font-size:11px;font-weight:600;color:#94a3b8;text-transform:uppercase;letter-spacing:.5px;background:#f8fafc;border-bottom:1px solid #e2e8f0;position:sticky;top:0}
        .bdi-table td{padding:10px 14px;border-bottom:1px solid #f1f5f9;color:#1e293b;vertical-align:middle}
        .bdi-table tr:last-child td{border-bottom:none}
        .bdi-table tr:hover td{background:#fafafa}
        .bdi-badge{display:inline-flex;align-items:center;gap:4px;padding:3px 9px;border-radius:20px;font-size:11px;font-weight:600}
        .bdi-badge.in{background:#dcfce7;color:#16a34a}
        .bdi-badge.out{background:#fee2e2;color:#ef4444}
        .bdi-empty{padding:28px;text-align:center;color:#94a3b8;font-size:13px}
        .bdi-mh-row{display:flex;align-items:center;justify-content:space-between;padding:9px 14px;border-bottom:1px solid #f1f5f9;font-size:13px}
        .bdi-mh-row:last-child{border-bottom:none}
        .bdi-section-title{padding:10px 14px 6px;font-size:11px;font-weight:700;color:#94a3b8;text-transform:uppercase;letter-spacing:.5px;background:#f8fafc;border-bottom:1px solid #e2e8f0}
        .bdi-refresh-lbl{font-size:11px;color:#94a3b8;margin-left:auto}
      `;
      document.head.appendChild(s);
    }

    // Build widget skeleton
    container.innerHTML = `
      <div class="bdi-widget">
        <div class="bdi-widget-head">
          <h3>🏗️ ${opts.title}</h3>
          <a href="${ADMIN_URL}" target="_blank">Open Admin Portal ↗</a>
        </div>
        <div class="bdi-widget-meta">
          <div class="bdi-dot off" id="_bdi_dot"></div>
          <span id="_bdi_status_lbl">Connecting…</span>
          <span class="bdi-refresh-lbl" id="_bdi_refresh_lbl"></span>
        </div>
        <div class="bdi-widget-body" id="_bdi_body">
          <div class="bdi-empty">Loading attendance data…</div>
        </div>
      </div>`;

    const dot    = container.querySelector('#_bdi_dot');
    const lbl    = container.querySelector('#_bdi_status_lbl');
    const body   = container.querySelector('#_bdi_body');
    const rLbl   = container.querySelector('#_bdi_refresh_lbl');

    // Helper: pair punches IN+OUT per employee
    function pairPunches(records) {
      const byEmp = {};
      records.forEach(r => {
        if (!byEmp[r.empUid]) byEmp[r.empUid] = { name: r.empName, ins: [], outs: [] };
        if (r.type === 'in')  byEmp[r.empUid].ins.push(r);
        else                  byEmp[r.empUid].outs.push(r);
      });
      const rows = [];
      Object.values(byEmp).forEach(emp => {
        const ins  = emp.ins.sort((a,b) => a.time < b.time ? -1 : 1);
        const outs = emp.outs.sort((a,b) => a.time < b.time ? -1 : 1);
        const max  = Math.max(ins.length, outs.length);
        for (let i = 0; i < max; i++) {
          rows.push({ name: emp.name, inRec: ins[i]||null, outRec: outs[i]||null });
        }
      });
      return rows;
    }

    function fmtTime(iso) {
      if (!iso) return '—';
      try { return new Date(iso).toLocaleTimeString([], {hour:'2-digit',minute:'2-digit'}); } catch { return iso; }
    }

    function gpsLink(rec) {
      if (!rec || rec.lat == null) return '<span style="color:#94a3b8;font-size:11px">No GPS</span>';
      return `<a href="https://maps.google.com/?q=${rec.lat},${rec.lng}" target="_blank" style="color:#2563eb;font-size:11px;text-decoration:none">📍 Map</a>`;
    }

    async function refresh() {
      try {
        // Check server status
        const status = await getStatus();
        dot.className = 'bdi-dot';
        lbl.textContent = `Online · ${status.enrolled||0} faces enrolled`;
        rLbl.textContent = `Updated ${new Date().toLocaleTimeString()}`;

        const records = await getTodayPunches(opts.siteId);
        const pairs   = pairPunches(records);

        if (pairs.length === 0) {
          body.innerHTML = `<div class="bdi-empty">No attendance records for today yet.</div>`;
          return;
        }

        // Punches table
        let html = `
          <div class="bdi-section-title">Today's Attendance</div>
          <table class="bdi-table">
            <thead><tr>
              <th>Employee</th>
              <th>Check In</th>
              <th>Check Out</th>
              <th>Site</th>
              <th>GPS</th>
            </tr></thead>
            <tbody>`;

        pairs.forEach(row => {
          const inTime   = fmtTime(row.inRec?.time);
          const outTime  = row.outRec ? fmtTime(row.outRec.time) : '<span style="color:#f59e0b;font-size:11px">⏳ Active</span>';
          const site     = (row.inRec?.siteName || row.outRec?.siteName || '—');
          const gps      = gpsLink(row.inRec);
          html += `<tr>
            <td style="font-weight:600">${row.name}</td>
            <td><span class="bdi-badge in">● ${inTime}</span></td>
            <td>${row.outRec ? `<span class="bdi-badge out">● ${outTime}</span>` : outTime}</td>
            <td style="color:#64748b;font-size:12px">${site}</td>
            <td>${gps}</td>
          </tr>`;
        });
        html += '</tbody></table>';

        // Man-hours section
        if (opts.showManHours) {
          const today   = new Date().toISOString().slice(0, 10);
          const manhours = await getManHours(today);

          if (manhours.length > 0) {
            html += `<div class="bdi-section-title" style="margin-top:4px">Man-Hours Summary</div>`;
            manhours.sort((a,b) => b.totalHours - a.totalHours).forEach(m => {
              const hrs  = Math.floor(m.totalHours);
              const mins = Math.round((m.totalHours - hrs) * 60);
              const bar  = Math.min(100, (m.totalHours / 10) * 100); // 10h = full bar
              html += `
                <div class="bdi-mh-row">
                  <div>
                    <div style="font-weight:600;font-size:13px">${m.empName}</div>
                    <div style="background:#e2e8f0;border-radius:4px;height:4px;width:120px;margin-top:4px;overflow:hidden">
                      <div style="background:#e87722;height:100%;width:${bar}%;border-radius:4px"></div>
                    </div>
                  </div>
                  <div style="font-weight:700;color:#0f172a;font-size:13px">${hrs}h ${mins}m</div>
                </div>`;
            });
          }
        }

        body.innerHTML = html;
      } catch (err) {
        dot.className = 'bdi-dot off';
        lbl.textContent = 'Server offline';
        body.innerHTML = `<div class="bdi-empty" style="color:#ef4444">⚠️ Cannot reach attendance server.<br><small>${err.message}</small></div>`;
      }
    }

    refresh();
    const timer = setInterval(refresh, opts.refreshSeconds * 1000);
    container._bdi_stop = () => clearInterval(timer);

    console.log(`[AttendanceBridge] Widget started — refreshing every ${opts.refreshSeconds}s`);
    return () => clearInterval(timer);
  }

  /* ── Public API ──────────────────────────────────────────────── */
  return {
    /* Core API methods */
    getStatus,
    syncEmployees,
    getEmployees,
    getTodayPunches,
    getPunchesByDate,
    getManHours,
    getSites,
    getEnrolledFaces,

    /* Live poll helper */
    pollPunches,

    /* Ready-made widget renderer */
    renderWidget,

    /* Direct links */
    adminUrl: BASE + '/admin',
    baseUrl:  BASE,
  };

})();

/* Auto-expose globally */
window.AttendanceBridge = AttendanceBridge;
console.log('[AttendanceBridge] Loaded — Railway:', AttendanceBridge.baseUrl);
