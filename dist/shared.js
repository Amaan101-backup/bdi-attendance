/* ══════════════════════════════════════════
   PWA — Service Worker + Install Prompt
══════════════════════════════════════════ */
let _pwaInstallPrompt = null;

if ('serviceWorker' in navigator) {
  navigator.serviceWorker.register('/sw.js').then(reg => {
    console.log('[PWA] Service worker registered:', reg.scope);
  }).catch(err => {
    console.warn('[PWA] Service worker failed:', err);
  });
}

// Capture the browser's native install prompt
window.addEventListener('beforeinstallprompt', e => {
  e.preventDefault();
  _pwaInstallPrompt = e;
  // Show install button if present on page
  const btn = document.getElementById('pwa-install-btn');
  if (btn) { btn.style.display = 'inline-flex'; }
});

// Called by the "Install App" button
function installPWA() {
  if (!_pwaInstallPrompt) {
    alert('To install:\n\nAndroid Chrome: Menu (⋮) → "Add to Home Screen"\niOS Safari: Share (□↑) → "Add to Home Screen"');
    return;
  }
  _pwaInstallPrompt.prompt();
  _pwaInstallPrompt.userChoice.then(result => {
    if (result.outcome === 'accepted') {
      console.log('[PWA] App installed!');
      const btn = document.getElementById('pwa-install-btn');
      if (btn) btn.style.display = 'none';
    }
    _pwaInstallPrompt = null;
  });
}

// Hide install button if already installed
window.addEventListener('appinstalled', () => {
  const btn = document.getElementById('pwa-install-btn');
  if (btn) btn.style.display = 'none';
  _pwaInstallPrompt = null;
  console.log('[PWA] App installed successfully');
});

/* ══════════════════════════════════════════
   PYTHON FACE SERVER BRIDGE
   Auto-detects face_server.py running on localhost:5000
   Falls back to browser face-api.js if server is offline
══════════════════════════════════════════ */
// Smart server URL detection:
// - Served by Flask locally (localhost:5000) → relative URLs
// - Served via Cloudflare / any web server (https://your-domain.com) → relative URLs
// - Opened as a local file (file://) → call localhost:5000 directly
const PY_SERVER = (location.protocol === 'file:')
  ? 'http://localhost:5000'
  : (location.hostname === 'localhost' || location.hostname === '127.0.0.1')
  ? ''
  : 'https://bdi-attendance-production-d7e9.up.railway.app';
let pyServerOnline = false;

async function pyPing() {
  const wasOnline = pyServerOnline;
  try {
    const r = await fetch(`${PY_SERVER}/status`, { signal: AbortSignal.timeout(1500) });
    const d = await r.json();
    pyServerOnline = !!d.ok;
  } catch { pyServerOnline = false; }
  _updatePyStatus();
  // Merge Python records whenever server just came online
  if(pyServerOnline && !wasOnline) _mergePyRecords();
  return pyServerOnline;
}

function _updatePyStatus() {
  document.querySelectorAll('.ai-status-lbl').forEach(el => {
    if (pyServerOnline) {
      el.textContent = '✓ Python Face Server Connected';
      el.style.color = '#22c55e';
    } else if (modelsLoaded) {
      el.textContent = '✓ AI Ready (Browser Mode)';
      el.style.color = '#f59e0b';
    }
  });
  // Show/hide the Python sync bar in the report tab
  const bar = document.getElementById('py-sync-bar');
  if(bar) bar.style.display = pyServerOnline ? 'flex' : 'none';
}

/* Grab a single frame from a video element as base64 JPEG */
function vidToBase64(vid, quality = 0.85) {
  const c = document.createElement('canvas');
  c.width  = vid.videoWidth  || 640;
  c.height = vid.videoHeight || 480;
  c.getContext('2d').drawImage(vid, 0, 0);
  return c.toDataURL('image/jpeg', quality);
}

/* ── Python: enroll face from video element ── */
async function pyEnroll(uid, name, vid) {
  const img = vidToBase64(vid);
  const r = await fetch(`${PY_SERVER}/enroll`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ uid, name, image_b64: img })
  });
  return r.json();
}

/* ── Python: 1:N identify (scanner) ── */
async function pyRecognize(vid, siteUids = null) {
  const img = vidToBase64(vid);
  const r = await fetch(`${PY_SERVER}/recognize`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ image_b64: img, threshold: 0.50, site_uids: siteUids })
  });
  return r.json();
}

/* ── Python: 1:1 verify (specific employee) ── */
async function pyVerify(uid, vid) {
  const img = vidToBase64(vid);
  const r = await fetch(`${PY_SERVER}/verify`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ uid, image_b64: img, threshold: 0.50 })
  });
  return r.json();
}

/* Ping on startup and every 10s */
pyPing();
setInterval(pyPing, 10000);

/* ══════════════════════════════════════════
   DATA
══════════════════════════════════════════ */
let EMPS=[], SITES=[], SUPS=[], RECS=[];
let curUser=null, curRole=null, loginRole='admin';
let sCamStream=null, sFaceOk=false, sGeoOk=false, sGeoData=null, sSite=null, selEmp=null, scanning=false;
let bulkSite=null;
let ec=100, sc=20;

/* ── SETTINGS ── */
const SETTINGS_DEFAULTS = {
  companyName: 'Bright Deal International',
  companyShort: 'BDI',
  faceThreshold: 0.55,   // 1:1 verification (strict)
  scanThreshold: 0.62,   // scanner/terminal auto-ID (lenient for real-world conditions)
  cooldownFrames: 60,
  confirmFrames: 3,      // consecutive matching frames before auto-punch
  minFaceRatio: 0.04,    // face must cover this % of frame (prevents distant-face misses)
  shiftStart: '08:00',
  shiftEnd: '17:00',
  lateGraceMinutes: 15,
  sessionTimeoutMin: 30
};
let SETTINGS = {...SETTINGS_DEFAULTS};
function loadSettings(){
  try{const s=localStorage.getItem('bdi_settings');if(s)SETTINGS={...SETTINGS_DEFAULTS,...JSON.parse(s)};}catch(e){console.warn('Settings load failed',e);}
}
function saveSettings(){
  try{localStorage.setItem('bdi_settings',JSON.stringify(SETTINGS));}catch(e){console.warn('Settings save failed',e);}
}

/* ── XSS PROTECTION ── */
const esc = s => String(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');

/* ── SESSION TIMEOUT ── */
let _lastActivity = Date.now();
document.addEventListener('mousemove', ()=>_lastActivity=Date.now(), {passive:true});
document.addEventListener('keydown', ()=>_lastActivity=Date.now(), {passive:true});
document.addEventListener('click', ()=>_lastActivity=Date.now(), {passive:true});
setInterval(()=>{
  if(!curUser||!SETTINGS.sessionTimeoutMin) return;
  if((Date.now()-_lastActivity)/60000 >= SETTINGS.sessionTimeoutMin){
    toast('Session expired due to inactivity', false);
    setTimeout(logout, 1500);
  }
}, 30000);

const DEMO_EMPS=[
  {name:'Ahmed Hassan',id:'BDI-001',dept:'Engineering',role:'Pipeline Engineer',phone:'+971501111001',sites:['S1','S2'],faceReg:false,descriptor:null},
  {name:'Fatima Al Ali',id:'BDI-002',dept:'HSE',role:'Safety Officer',phone:'+971501111002',sites:['S1'],faceReg:false,descriptor:null},
  {name:'Khalid Mansoor',id:'BDI-003',dept:'Operations',role:'Site Foreman',phone:'+971501111003',sites:['S1','S3'],faceReg:false,descriptor:null},
  {name:'Sara Ibrahim',id:'BDI-004',dept:'Engineering',role:'Surveyor',phone:'+971501111004',sites:['S2'],faceReg:false,descriptor:null},
  {name:'Yusuf Al Nasser',id:'BDI-005',dept:'Mechanical',role:'Technician',phone:'+971501111005',sites:['S1','S2','S3'],faceReg:false,descriptor:null},
  {name:'James Okafor',id:'BDI-006',dept:'Operations',role:'Welder',phone:'+971501111006',sites:['S3'],faceReg:false,descriptor:null},
  {name:'Ravi Sharma',id:'BDI-007',dept:'Engineering',role:'Civil Engineer',phone:'+971501111007',sites:['S2','S3'],faceReg:false,descriptor:null},
  {name:'Amina Diallo',id:'BDI-008',dept:'Admin',role:'Document Controller',phone:'+971501111008',sites:['S1'],faceReg:false,descriptor:null},
  {name:'Carlos Mendes',id:'BDI-009',dept:'Operations',role:'Equipment Operator',phone:'+971501111009',sites:['S3'],faceReg:false,descriptor:null},
  {name:'Nadia Hassan',id:'BDI-010',dept:'Engineering',role:'Structural Engineer',phone:'+971501111010',sites:['S1','S2'],faceReg:false,descriptor:null},
];

SITES=[
  {id:'S1',code:'ABD-HQ',name:'Abu Dhabi HQ Office',loc:'MBZ City, Abu Dhabi',lat:24.4539,lng:54.3773,radius:3},
  {id:'S2',code:'ABD-CW',name:'Corniche Pipeline Works',loc:'Corniche Road, Abu Dhabi',lat:24.4672,lng:54.3686,radius:2},
  {id:'S3',code:'SHJ-IND',name:'Sharjah Industrial Zone',loc:'Industrial Area, Sharjah',lat:25.3462,lng:55.4209,radius:3},
  {id:'S4',code:'AIN-01',name:'Al Ain Pipeline Project',loc:'Al Ain, Abu Dhabi',lat:24.2070,lng:55.7435,radius:4},
];

SUPS=[
  {uid:'SUP1',name:'Omar Al Rashidi',loginId:'sup001',role:'Site Supervisor',pw:'sup001',sites:['S1','S2']},
  {uid:'SUP2',name:'Mohammed Khalfan',loginId:'sup002',role:'Site Engineer',pw:'sup002',sites:['S3']},
  {uid:'SUP3',name:'Priya Nair',loginId:'sup003',role:'HSE Officer',pw:'sup003',sites:['S1','S3','S4']},
];

/* ── UTILS ── */
const ini=n=>n.trim().split(' ').map(w=>w[0]).join('').slice(0,2).toUpperCase();
const hav=(a,b,c,d)=>{const R=6371,dL=(c-a)*Math.PI/180,dl=(d-b)*Math.PI/180;const x=Math.sin(dL/2)**2+Math.cos(a*Math.PI/180)*Math.cos(c*Math.PI/180)*Math.sin(dl/2)**2;return R*2*Math.atan2(Math.sqrt(x),Math.sqrt(1-x));};
const dCls=d=>({'Engineering':'db-e','Operations':'db-o','HSE':'db-h','Mechanical':'db-m','Admin':'db-a'}[d]||'db-x');
function toast(msg,ok=true){const t=document.getElementById('toast');t.innerHTML=`<i class="ti ti-${ok?'check':'alert-triangle'}" style="font-size:12px"></i> ${msg}`;t.style.borderLeftColor=ok?'var(--orange)':'var(--err)';t.style.display='block';setTimeout(()=>t.style.display='none',2800);}
function showScr(id){document.querySelectorAll('.screen').forEach(s=>s.classList.remove('active'));document.getElementById(id).classList.add('active');}
function siteById(id){return SITES.find(s=>s.id===id);}

/* ── LOGIN ── */
function setRole(r){
  loginRole=r;
  document.getElementById('rc-admin').classList.toggle('active',r==='admin');
  document.getElementById('rc-sup').classList.toggle('active',r==='supervisor');
  document.getElementById('rc-emp').classList.toggle('active',r==='employee');
  const hints={admin:'Admin: admin / bdi2026',supervisor:'Demo supervisors: sup001/sup001 · sup002/sup002 · sup003/sup003',employee:'Employee self-marking is disabled. Your supervisor marks your attendance.'};
  document.getElementById('l-hint').textContent=hints[r]||'';
}

function doLogin(){
  const u=document.getElementById('l-u').value.trim();
  const p=document.getElementById('l-p').value;
  const err=document.getElementById('l-err');
  err.style.display='none';
  if(loginRole==='admin'){
    if(u==='admin'&&p==='bdi2026'){curUser={name:'Admin'};curRole='admin';showScr('scr-admin');initAdmin();}
    else{err.textContent='Invalid credentials';err.style.display='block';}
    return;
  }
  if(loginRole==='supervisor'){
    const sup=SUPS.find(s=>s.loginId.toLowerCase()===u.toLowerCase()&&s.pw===p);
    if(sup){curUser=sup;curRole='supervisor';showScr('scr-sup');initSup();}
    else{err.textContent='Supervisor ID or password incorrect';err.style.display='block';}
    return;
  }
  if(loginRole==='employee'){
    err.style.color='#93c5fd';
    err.textContent='Employee self-marking is not available. Your Site Supervisor or Site Engineer marks attendance on your behalf.';
    err.style.display='block';
  }
}

function logout(){
  curUser=null;curRole=null;sFaceOk=false;sGeoOk=false;sGeoData=null;selEmp=null;sSite=null;bulkSite=null;
  if(sCamStream){sCamStream.getTracks().forEach(t=>t.stop());sCamStream=null;}
  const _lu=document.getElementById('l-u'); if(_lu) _lu.value='';
  const _lp=document.getElementById('l-p'); if(_lp) _lp.value='';
  if(document.getElementById('scr-login')) showScr('scr-login');
  else window.location.href='attendance.html';
}

/* Safe login key-listener — only runs on pages that have the login form */
const _lpEl=document.getElementById('l-p');
if(_lpEl) _lpEl.addEventListener('keydown',e=>{if(e.key==='Enter')doLogin();});

/* ══════════════════════════════════════════
   ADMIN
══════════════════════════════════════════ */
function initAdmin(){
  renderAStats();renderEmpTbl();renderSupTbl();renderSites();populateAllSelects();
  // Set default date range to today
  const today=new Date().toISOString().slice(0,10);
  const df=document.getElementById('r-date-from');if(df)df.value=today;
  const dt=document.getElementById('r-date-to');if(dt)dt.value=today;
}

function aTab(t){
  ['emp','sups','sites','enroll','term','report','dash','settings'].forEach(k=>{
    const el=document.getElementById('ap-'+k); if(el) el.classList.toggle('active',k===t);
  });
  document.querySelectorAll('#scr-admin .stab').forEach(b=>b.classList.remove('active'));
  document.querySelectorAll('#scr-admin .stab').forEach(b=>{ if(b.onclick && b.onclick.toString().includes(`'${t}'`)) b.classList.add('active'); });
  if(t==='report'){renderReport();populateRepFilter();}
  if(t==='sites')renderSites();
  if(t==='sups')renderSupTbl();
  if(t==='enroll')initEnroll();
  if(t==='term')initTerm();
  if(t==='dash')renderDashboard();
  if(t==='settings')initSettings();
}

function populateAllSelects(){
  const opts=SITES.map(s=>`<option value="${s.id}">${s.name} (${s.code})</option>`).join('');
  document.getElementById('esites').innerHTML=opts;
  document.getElementById('ssites').innerHTML=opts;
  populateRepFilter();
}

function populateRepFilter(){
  const rf=document.getElementById('rsiteF');
  if(rf)rf.innerHTML='<option value="">All sites</option>'+SITES.map(s=>`<option value="${s.id}">${s.name}</option>`).join('');
}

function renderAStats(){
  const total=EMPS.length;
  const pSet=new Set();
  EMPS.forEach(e=>{const r=RECS.filter(x=>x.empUid===e.uid);if(r.length&&r[r.length-1].type==='in')pSet.add(e.uid);});
  const faceReg=EMPS.filter(e=>e.faceReg).length;
  const el=document.getElementById('a-stats');if(!el)return;
  el.innerHTML=`
    <div class="stat"><div class="stat-n">${total}</div><div class="stat-l">Total Employees</div></div>
    <div class="stat"><div class="stat-n" style="color:var(--ok)">${pSet.size}</div><div class="stat-l">Present Today</div></div>
    <div class="stat"><div class="stat-n" style="color:var(--orange)">${SUPS.length}</div><div class="stat-l">Supervisors</div></div>
    <div class="stat"><div class="stat-n" style="color:var(--infotxt)">${total?Math.round(pSet.size/total*100):0}%</div><div class="stat-l">Attendance Rate</div></div>`;
}

function loadDemo(){
  if(EMPS.length&&!confirm('Replace current employees with demo data?'))return;
  EMPS=DEMO_EMPS.map(e=>({...e,uid:'E'+(++ec)}));
  renderAStats();renderEmpTbl();toast('10 demo employees loaded');
}

function addEmp(){
  const name=document.getElementById('en').value.trim();
  const id=document.getElementById('ei').value.trim();
  const dept=document.getElementById('ed').value;
  const role=document.getElementById('er').value.trim()||dept;
  const phone=document.getElementById('eph').value.trim();
  const sites=[...document.getElementById('esites').selectedOptions].map(o=>o.value).filter(Boolean);
  const faceReg=document.querySelector('input[name="eface"]:checked')?.value==='yes';
  if(!name||!id){toast('Name and Employee ID are required',false);return;}
  if(EMPS.find(e=>e.id.toUpperCase()===id.toUpperCase())){toast('Employee ID already exists',false);return;}
  EMPS.push({uid:'E'+(++ec),name,id,dept,role,phone,sites,faceReg});
  ['en','ei','er','eph'].forEach(x=>document.getElementById(x).value='');
  renderAStats();renderEmpTbl();toast(`${name} added to company directory`);
}

function delEmp(uid){
  const e=EMPS.find(x=>x.uid===uid);
  if(!e||!confirm(`Remove ${e.name} from the directory?`))return;
  EMPS=EMPS.filter(x=>x.uid!==uid);RECS=RECS.filter(r=>r.empUid!==uid);
  renderAStats();renderEmpTbl();toast(`${e.name} removed`,false);
}

function renderEmpTbl(){
  const q=(document.getElementById('esrch').value||'').toLowerCase();
  const df=document.getElementById('edept').value;
  const list=EMPS.filter(e=>(!q||(e.name.toLowerCase().includes(q)||e.id.toLowerCase().includes(q)||e.role.toLowerCase().includes(q)))&&(!df||e.dept===df));
  const tb=document.getElementById('etbl');
  if(!list.length){tb.innerHTML=`<tr><td colspan="8"><div class="empty"><i class="ti ti-search"></i>No employees found</div></td></tr>`;return;}
  tb.innerHTML=list.map(e=>{
    const recs=RECS.filter(r=>r.empUid===e.uid);
    const isIn=recs.length&&recs[recs.length-1].type==='in';
    const siteNames=(e.sites||[]).map(id=>SITES.find(s=>s.id===id)?.code||id).join(', ')||'—';
    return `<tr>
      <td><div style="display:flex;align-items:center;gap:6px"><div class="av">${ini(e.name)}</div><div><div style="font-weight:500;color:var(--navy)">${esc(e.name)}</div><div style="font-size:10px;color:var(--gray)">${esc(e.phone||'')}</div></div></div></td>
      <td><span class="site-badge">${esc(e.id)}</span></td>
      <td><span class="${dCls(e.dept)}">${esc(e.dept)}</span></td>
      <td>${esc(e.role)}</td>
      <td style="font-size:10px;color:var(--dg)">${esc(siteNames)}</td>
      <td>${(e.faceReg&&e.descriptor)?'<span class="face-pill-ok"><i class="ti ti-circle-check" style="font-size:10px"></i> Enrolled</span>':'<span class="face-pill-no"><i class="ti ti-alert-circle" style="font-size:10px"></i> Not Enrolled</span>'}</td>
      <td>${recs.length?`<span class="${isIn?'in-b':'out-b'}">${isIn?'IN':'OUT'}</span>`:'<span style="color:var(--gray);font-size:10px">Absent</span>'}</td>
      <td><button class="btn r sm" onclick="delEmp('${esc(e.uid)}')"><i class="ti ti-trash"></i></button></td>
    </tr>`;
  }).join('');
}

function addSup(){
  const name=document.getElementById('sn').value.trim();
  const loginId=document.getElementById('sid').value.trim();
  const role=document.getElementById('srole').value;
  const pw=document.getElementById('spw').value;
  const sites=[...document.getElementById('ssites').selectedOptions].map(o=>o.value).filter(Boolean);
  if(!name||!loginId||!pw){toast('Fill all required fields',false);return;}
  if(SUPS.find(s=>s.loginId.toLowerCase()===loginId.toLowerCase())){toast('Login ID already exists',false);return;}
  SUPS.push({uid:'SUP'+(++sc),name,loginId,role,pw,sites});
  ['sn','sid','spw'].forEach(x=>document.getElementById(x).value='');
  renderSupTbl();renderAStats();toast(`${name} added as ${role}`);
}

function delSup(uid){
  const s=SUPS.find(x=>x.uid===uid);
  if(!s||!confirm(`Remove ${s.name}?`))return;
  SUPS=SUPS.filter(x=>x.uid!==uid);renderSupTbl();renderAStats();toast(`${s.name} removed`,false);
}

function renderSupTbl(){
  const tb=document.getElementById('suptbl');
  if(!SUPS.length){tb.innerHTML=`<tr><td colspan="6"><div class="empty"><i class="ti ti-user-x"></i>No supervisors added yet</div></td></tr>`;return;}
  tb.innerHTML=SUPS.map(s=>{
    const siteNames=s.sites.map(id=>SITES.find(x=>x.id===id)?.code||id).join(', ')||'None assigned';
    const marked=RECS.filter(r=>r.markedBy===s.uid).length;
    return `<tr>
      <td><div style="display:flex;align-items:center;gap:6px"><div class="av sup">${ini(s.name)}</div><div style="font-weight:500;color:var(--navy)">${esc(s.name)}</div></div></td>
      <td><span class="site-badge">${esc(s.loginId)}</span></td>
      <td><span class="role-pill">${esc(s.role)}</span></td>
      <td style="font-size:10px;color:var(--dg)">${esc(siteNames)}</td>
      <td><span style="font-weight:500;color:${marked>0?'var(--ok)':'var(--gray)'}">${marked} record${marked!==1?'s':''}</span></td>
      <td><button class="btn r sm" onclick="delSup('${esc(s.uid)}')"><i class="ti ti-trash"></i></button></td>
    </tr>`;
  }).join('');
}

function addSite(){
  const name=document.getElementById('sns').value.trim();
  const code=document.getElementById('snc').value.trim().toUpperCase();
  const loc=document.getElementById('snl').value.trim();
  const lat=parseFloat(document.getElementById('snlat').value);
  const lng=parseFloat(document.getElementById('snlng').value);
  const radius=parseFloat(document.getElementById('srad').value);
  if(!name||!code||isNaN(lat)||isNaN(lng)){toast('Fill all required fields',false);return;}
  if(SITES.find(s=>s.code===code)){toast('Site code already exists',false);return;}
  SITES.push({id:'S'+Date.now(),code,name,loc:loc||'UAE',lat,lng,radius});
  ['sns','snc','snl','snlat','snlng'].forEach(x=>document.getElementById(x).value='');
  document.getElementById('srad').value=2;document.getElementById('srad-v').textContent='2.0 km';
  renderSites();populateAllSelects();toast(`${name} added`);
}

function useMyLoc(){
  const msg=document.getElementById('loc-msg');msg.style.display='block';msg.textContent='Acquiring GPS…';
  navigator.geolocation.getCurrentPosition(pos=>{
    document.getElementById('snlat').value=pos.coords.latitude.toFixed(5);
    document.getElementById('snlng').value=pos.coords.longitude.toFixed(5);
    msg.textContent=`✓ ${pos.coords.latitude.toFixed(4)}, ${pos.coords.longitude.toFixed(4)} (±${Math.round(pos.coords.accuracy)}m)`;
    msg.style.color='var(--ok)';
  },()=>{msg.textContent='GPS unavailable — enter coordinates manually';msg.style.color='var(--err)';});
}

// editRad and delSite are now handled inside the site edit modal (openSiteModal / mDeleteSite)

function renderSites(){
  const el=document.getElementById('sites-list');if(!el)return;
  if(!SITES.length){el.innerHTML='<div class="empty"><i class="ti ti-building-off"></i>No sites added yet</div>';return;}
  el.innerHTML=SITES.map(s=>{
    const sr=RECS.filter(r=>r.siteId===s.id);
    const pSet=new Set(sr.filter(r=>r.type==='in').map(r=>r.empUid));
    sr.filter(r=>r.type==='out').forEach(r=>pSet.delete(r.empUid));
    const assignedSups=SUPS.filter(x=>x.sites.includes(s.id));
    const assignedEmps=EMPS.filter(e=>(e.sites||[]).includes(s.id));
    const supNames=assignedSups.length?assignedSups.map(x=>`<span style="display:inline-flex;align-items:center;gap:4px;background:rgba(232,119,34,.1);color:var(--orange);border:1px solid rgba(232,119,34,.25);border-radius:20px;padding:2px 8px;font-size:10px;font-weight:500;margin-right:3px"><i class="ti ti-hard-hat" style="font-size:10px"></i>${esc(x.name)}</span>`).join(''):'<span style="color:var(--err);font-size:10px"><i class="ti ti-alert-circle" style="font-size:11px"></i> No supervisor assigned</span>';
    return `<div class="card" style="border-left:3px solid var(--orange)">
      <div style="display:flex;align-items:flex-start;justify-content:space-between;flex-wrap:wrap;gap:8px">
        <div style="display:flex;align-items:center;gap:10px;flex:1">
          <span class="site-badge" style="font-size:11px;padding:4px 10px">${esc(s.code)}</span>
          <div style="flex:1">
            <div style="font-weight:500;color:var(--navy);font-size:13px;margin-bottom:3px">${esc(s.name)}</div>
            <div style="font-size:10px;color:var(--gray);margin-bottom:5px"><i class="ti ti-map-pin" style="font-size:10px"></i> ${esc(s.loc)} &nbsp;·&nbsp; <span style="font-family:var(--mono)">${s.lat.toFixed(4)}, ${s.lng.toFixed(4)}</span> &nbsp;·&nbsp; <span style="background:var(--infobg);color:var(--infotxt);padding:1px 6px;border-radius:20px">${s.radius} km zone</span></div>
            <div style="display:flex;align-items:center;gap:5px;flex-wrap:wrap">
              <span style="font-size:10px;color:var(--dg);font-weight:500;margin-right:2px"><i class="ti ti-hard-hat" style="font-size:11px;color:var(--orange)"></i> Supervisors:</span>
              ${supNames}
            </div>
            <div style="margin-top:5px;font-size:10px;color:var(--dg)">
              <i class="ti ti-users" style="font-size:11px;color:var(--navy)"></i>
              <span style="font-weight:500">${assignedEmps.length} employees</span> assigned
              &nbsp;·&nbsp; <span style="color:var(--ok);font-weight:500">${pSet.size} present today</span>
            </div>
          </div>
        </div>
        <div style="display:flex;align-items:center;gap:6px;flex-shrink:0">
          <button class="btn sm o" onclick="openSiteModal('${s.id}')"><i class="ti ti-edit"></i> Edit Site</button>
          <a href="https://maps.google.com/?q=${s.lat},${s.lng}" target="_blank" class="btn sm"><i class="ti ti-map"></i> Map</a>
        </div>
      </div>
    </div>`;
  }).join('');
}

/* ══════════════════════════════════════════
   SITE EDIT MODAL
══════════════════════════════════════════ */
let editingSiteId=null;

function openSiteModal(siteId){
  const s=SITES.find(x=>x.id===siteId);if(!s)return;
  editingSiteId=siteId;
  // populate site details
  document.getElementById('m-code').textContent=s.code;
  document.getElementById('m-name').value=s.name;
  document.getElementById('m-codeinput').value=s.code;
  document.getElementById('m-loc').value=s.loc||'';
  document.getElementById('m-lat').value=s.lat;
  document.getElementById('m-lng').value=s.lng;
  document.getElementById('m-rad').value=s.radius;
  document.getElementById('m-rad-v').textContent=parseFloat(s.radius).toFixed(1)+' km';
  document.getElementById('m-loc-msg').textContent='';
  document.getElementById('m-emp-srch').value='';
  renderMSupList();
  renderMEmpList();
  document.getElementById('site-modal').classList.add('open');
}

function closeSiteModal(e){
  if(e&&e.target!==document.getElementById('site-modal'))return;
  document.getElementById('site-modal').classList.remove('open');
  editingSiteId=null;
  renderSites();
}

function renderMSupList(){
  const el=document.getElementById('m-sup-list');
  if(!SUPS.length){el.innerHTML='<div style="color:var(--gray);font-size:11px;padding:8px;text-align:center">No supervisors exist yet — add them in the Supervisors tab first</div>';return;}
  const s=SITES.find(x=>x.id===editingSiteId);
  el.innerHTML=SUPS.map(sup=>{
    const assigned=s&&s.supervisors&&s.supervisors.includes(sup.uid);
    // Also check legacy: supervisor has this site in their own sites array
    const legacyAssigned=sup.sites.includes(editingSiteId);
    const isOn=assigned||legacyAssigned;
    return `<div class="sup-assign-row ${isOn?'assigned':''}" id="msr-${esc(sup.uid)}">
      <div class="sup-info">
        <div class="av sup" style="width:30px;height:30px;font-size:10px">${ini(sup.name)}</div>
        <div>
          <div style="font-weight:500;color:var(--navy)">${esc(sup.name)}</div>
          <div style="font-size:10px;color:var(--gray)">${esc(sup.loginId)} &nbsp;·&nbsp; <span class="role-pill" style="font-size:9px">${esc(sup.role)}</span></div>
          <div style="font-size:10px;color:var(--gray);margin-top:1px">Also on: ${sup.sites.filter(id=>id!==editingSiteId).map(id=>esc(SITES.find(s=>s.id===id)?.code||id)).join(', ')||'no other sites'}</div>
        </div>
      </div>
      <button class="btn sm ${isOn?'r':'g'}" onclick="toggleSupOnSite('${esc(sup.uid)}')">
        ${isOn?'<i class="ti ti-user-minus"></i> Remove':'<i class="ti ti-user-plus"></i> Assign'}
      </button>
    </div>`;
  }).join('');
}

function toggleSupOnSite(supUid){
  const sup=SUPS.find(x=>x.uid===supUid);if(!sup||!editingSiteId)return;
  if(sup.sites.includes(editingSiteId)){
    sup.sites=sup.sites.filter(id=>id!==editingSiteId);
    toast(`${sup.name} removed from this site`,false);
  } else {
    sup.sites.push(editingSiteId);
    toast(`${sup.name} assigned to this site`);
  }
  renderMSupList();
}

function renderMEmpList(){
  const el=document.getElementById('m-emp-list');
  const q=(document.getElementById('m-emp-srch').value||'').toLowerCase();
  const filtered=EMPS.filter(e=>!q||(e.name.toLowerCase().includes(q)||e.id.toLowerCase().includes(q)||e.dept.toLowerCase().includes(q)));
  const total=filtered.length;
  const assigned=filtered.filter(e=>(e.sites||[]).includes(editingSiteId)).length;
  document.getElementById('m-emp-count').textContent=total;
  document.getElementById('m-emp-assigned-count').textContent=assigned+' assigned';
  if(!EMPS.length){el.innerHTML='<div style="color:var(--gray);font-size:11px;padding:8px;text-align:center">No employees yet — add them in the Employees tab first</div>';return;}
  if(!filtered.length){el.innerHTML='<div style="color:var(--gray);font-size:11px;padding:8px;text-align:center">No employees match your search</div>';return;}
  el.innerHTML=filtered.map(e=>{
    const isOn=(e.sites||[]).includes(editingSiteId);
    return `<div class="emp-assign-row ${isOn?'assigned':''}" id="mer-${esc(e.uid)}">
      <div style="display:flex;align-items:center;gap:7px">
        <div class="av" style="width:24px;height:24px;font-size:9px">${ini(e.name)}</div>
        <div>
          <div style="font-weight:500;color:var(--navy)">${esc(e.name)}</div>
          <div style="font-size:10px;color:var(--gray)">${esc(e.id)} &nbsp;·&nbsp; ${esc(e.dept)} &nbsp;·&nbsp; ${esc(e.role)}</div>
        </div>
      </div>
      <div style="display:flex;align-items:center;gap:5px">
        ${e.faceReg?'<span class="face-pill-ok" style="font-size:9px">Face ✓</span>':'<span class="face-pill-no" style="font-size:9px">No face</span>'}
        <button class="btn sm ${isOn?'r':'g'}" onclick="toggleEmpOnSite('${esc(e.uid)}')">${isOn?'<i class="ti ti-minus"></i> Remove':'<i class="ti ti-plus"></i> Assign'}</button>
      </div>
    </div>`;
  }).join('');
}

function toggleEmpOnSite(empUid){
  const emp=EMPS.find(x=>x.uid===empUid);if(!emp||!editingSiteId)return;
  if(!emp.sites)emp.sites=[];
  if(emp.sites.includes(editingSiteId)){
    emp.sites=emp.sites.filter(id=>id!==editingSiteId);
  } else {
    emp.sites.push(editingSiteId);
  }
  renderMEmpList();
}

function mAssignAll(){
  const q=(document.getElementById('m-emp-srch').value||'').toLowerCase();
  EMPS.filter(e=>!q||(e.name.toLowerCase().includes(q)||e.id.toLowerCase().includes(q))).forEach(e=>{
    if(!e.sites)e.sites=[];
    if(!e.sites.includes(editingSiteId))e.sites.push(editingSiteId);
  });
  renderMEmpList();toast('All visible employees assigned');
}

function mRemoveAll(){
  if(!confirm('Remove all employees from this site?'))return;
  EMPS.forEach(e=>{if(e.sites)e.sites=e.sites.filter(id=>id!==editingSiteId);});
  renderMEmpList();toast('All employees removed from this site',false);
}

function mUseMyLoc(){
  const msg=document.getElementById('m-loc-msg');msg.textContent='Acquiring…';
  navigator.geolocation.getCurrentPosition(pos=>{
    document.getElementById('m-lat').value=pos.coords.latitude.toFixed(5);
    document.getElementById('m-lng').value=pos.coords.longitude.toFixed(5);
    msg.textContent=`✓ ${pos.coords.latitude.toFixed(4)}, ${pos.coords.longitude.toFixed(4)}`;
    msg.style.color='var(--ok)';
  },()=>{msg.textContent='GPS unavailable';msg.style.color='var(--err)';});
}

function mDeleteSite(){
  const s=SITES.find(x=>x.id===editingSiteId);if(!s)return;
  if(!confirm(`Delete site "${s.name}"?\n\nThis will remove all attendance records for this site and cannot be undone.`))return;
  SITES=SITES.filter(x=>x.id!==editingSiteId);
  RECS=RECS.filter(r=>r.siteId!==editingSiteId);
  SUPS.forEach(sup=>{sup.sites=sup.sites.filter(id=>id!==editingSiteId);});
  EMPS.forEach(e=>{if(e.sites)e.sites=e.sites.filter(id=>id!==editingSiteId);});
  document.getElementById('site-modal').classList.remove('open');
  editingSiteId=null;
  renderSites();populateAllSelects();renderAStats();
  toast('Site deleted',false);
}

function saveSiteModal(){
  const s=SITES.find(x=>x.id===editingSiteId);if(!s)return;
  const name=document.getElementById('m-name').value.trim();
  const code=document.getElementById('m-codeinput').value.trim().toUpperCase();
  const loc=document.getElementById('m-loc').value.trim();
  const lat=parseFloat(document.getElementById('m-lat').value);
  const lng=parseFloat(document.getElementById('m-lng').value);
  const radius=parseFloat(document.getElementById('m-rad').value);
  if(!name||!code){toast('Site name and code are required',false);return;}
  if(isNaN(lat)||isNaN(lng)){toast('Valid latitude and longitude are required',false);return;}
  const codeConflict=SITES.find(x=>x.id!==editingSiteId&&x.code===code);
  if(codeConflict){toast('Site code already used by another site',false);return;}
  s.name=name;s.code=code;s.loc=loc||s.loc;s.lat=lat;s.lng=lng;s.radius=radius;
  document.getElementById('site-modal').classList.remove('open');
  editingSiteId=null;
  renderSites();populateAllSelects();
  // refresh supervisor site selector if logged in as supervisor
  if(curRole==='supervisor'&&curUser){initSup();}
  toast(`${name} saved successfully`);
}

function renderReport(){
  const total=EMPS.length;
  const pSet=new Set();EMPS.forEach(e=>{const r=RECS.filter(x=>x.empUid===e.uid);if(r.length&&r[r.length-1].type==='in')pSet.add(e.uid);});
  // Date range
  const dateFrom=document.getElementById('r-date-from')?.value;
  const dateTo=document.getElementById('r-date-to')?.value;
  const fromTs=dateFrom?new Date(dateFrom+'T00:00:00').getTime():null;
  const toTs=dateTo?new Date(dateTo+'T23:59:59').getTime():null;
  // Late/on-time counts within date range
  let filteredInRecs=RECS.filter(r=>r.type==='in');
  if(fromTs)filteredInRecs=filteredInRecs.filter(r=>r.time.getTime()>=fromTs);
  if(toTs)filteredInRecs=filteredInRecs.filter(r=>r.time.getTime()<=toTs);
  const lateCount=filteredInRecs.filter(r=>r.timeStatus?.cls==='late-b').length;
  const onTimeCount=filteredInRecs.filter(r=>r.timeStatus?.cls==='in-b'||r.timeStatus?.cls==='early-b').length;
  const rs=document.getElementById('rep-stats');
  if(rs)rs.innerHTML=`
    <div class="stat"><div class="stat-n">${total}</div><div class="stat-l">Total Employees</div></div>
    <div class="stat"><div class="stat-n" style="color:var(--ok)">${pSet.size}</div><div class="stat-l">Currently Present</div></div>
    <div class="stat"><div class="stat-n" style="color:var(--err)">${total-pSet.size}</div><div class="stat-l">Absent</div></div>
    <div class="stat"><div class="stat-n" style="color:var(--orange)">${total?Math.round(pSet.size/total*100):0}%</div><div class="stat-l">Rate</div></div>
    <div class="stat"><div class="stat-n" style="color:#ef4444">${lateCount}</div><div class="stat-l">Late</div></div>
    <div class="stat"><div class="stat-n" style="color:var(--ok)">${onTimeCount}</div><div class="stat-l">On Time</div></div>`;
  const q=(document.getElementById('rsrch').value||'').toLowerCase();
  const sf=document.getElementById('rsiteF').value;
  const stf=document.getElementById('rstatF').value;
  const tb=document.getElementById('rtbl');
  const rows=EMPS.map(e=>{
    if(q&&!e.name.toLowerCase().includes(q)&&!e.id.toLowerCase().includes(q))return '';
    let recs=RECS.filter(r=>r.empUid===e.uid);
    if(sf)recs=recs.filter(r=>r.siteId===sf);
    if(fromTs)recs=recs.filter(r=>r.time.getTime()>=fromTs);
    if(toTs)recs=recs.filter(r=>r.time.getTime()<=toTs);
    const fi=recs.find(r=>r.type==='in');
    const lo=[...recs].reverse().find(r=>r.type==='out');
    const st=recs.length?recs[recs.length-1].type:'absent';
    if(stf==='in'&&st!=='in')return '';
    if(stf==='out'&&st!=='out')return '';
    if(stf==='absent'&&st!=='absent')return '';
    const sv=[...new Set(recs.map(r=>r.siteCode))];
    const isPython = fi && fi.source==='python';
    const pb = isPython
      ? '<span style="color:#3b82f6;font-size:10px;font-weight:600"><i class="ti ti-brand-python"></i> Python Camera</span>'
      : fi ? (SUPS.find(s=>s.uid===fi.markedBy)?.name||fi.markedByName||'Unknown') : '—';
    const meth=fi?fi.method:'—';
    const conf=fi?.confidence ? `<span style="font-size:9px;color:#6b7280;margin-left:4px">${parseFloat(fi.confidence).toFixed(0)}%</span>` : '';
    const ts=fi?.timeStatus;
    return `<tr${isPython?' style="background:#eff6ff"':''}>
      <td><div style="display:flex;align-items:center;gap:6px"><div class="av">${ini(e.name)}</div><div style="font-weight:500;color:var(--navy)">${esc(e.name)}</div></div></td>
      <td><span class="site-badge">${esc(e.id)}</span></td>
      <td><span class="${dCls(e.dept)}">${esc(e.dept)}</span></td>
      <td>${sv.map(c=>`<span class="site-badge" style="margin-right:2px">${esc(c)}</span>`).join('')||'—'}</td>
      <td>${pb}</td>
      <td><span class="method-pill">${esc(meth)}</span>${conf}</td>
      <td style="color:var(--dg)">${fi?fi.time.toLocaleTimeString('en-US',{hour:'2-digit',minute:'2-digit'}):'—'}</td>
      <td style="color:var(--dg)">${lo?lo.time.toLocaleTimeString('en-US',{hour:'2-digit',minute:'2-digit'}):'—'}</td>
      <td>${ts?`<span class="${ts.cls}">${ts.label}</span>`:(st==='in'?'<span class="in-b">PRESENT</span>':st==='out'?'<span class="out-b">OUT</span>':'<span class="abs-b">ABSENT</span>')}</td>
      <td>${st==='in'?'<span class="in-b">IN</span>':st==='out'?'<span class="out-b">OUT</span>':'<span class="abs-b">ABSENT</span>'}</td>
    </tr>`;
  }).join('');
  tb.innerHTML=rows||`<tr><td colspan="10"><div class="empty"><i class="ti ti-search"></i>No results</div></td></tr>`;
}

/* ── LIVE DASHBOARD ── */
function renderDashboard() {
  const el = document.getElementById('ap-dash'); if(!el) return;
  const totalEmps = EMPS.length;
  const presentSet = new Set();
  const lateSet = new Set();
  EMPS.forEach(e=>{
    const recs = RECS.filter(r=>r.empUid===e.uid);
    const last = recs[recs.length-1];
    if(last && last.type==='in') {
      presentSet.add(e.uid);
      const firstIn = recs.find(r=>r.type==='in');
      if(firstIn?.timeStatus?.cls==='late-b') lateSet.add(e.uid);
    }
  });
  const absent = totalEmps - presentSet.size;
  const rate = totalEmps ? Math.round(presentSet.size/totalEmps*100) : 0;
  const siteBreakdown = SITES.map(s=>{
    const sEmps = EMPS.filter(e=>(e.sites||[]).includes(s.id));
    const sPresent = new Set();
    sEmps.forEach(e=>{
      const recs = RECS.filter(r=>r.empUid===e.uid && r.siteId===s.id);
      const last = recs[recs.length-1];
      if(last&&last.type==='in') sPresent.add(e.uid);
    });
    return {site:s, total:sEmps.length, present:sPresent.size, absent:sEmps.length-sPresent.size};
  });
  const todayIn = RECS.filter(r=>r.type==='in');
  const lateCount = todayIn.filter(r=>r.timeStatus?.cls==='late-b').length;
  const onTimeCount = todayIn.filter(r=>r.timeStatus?.cls==='in-b' || r.timeStatus?.cls==='early-b').length;
  el.innerHTML = `
    <div class="stats-row" style="margin-bottom:16px">
      <div class="stat"><div class="stat-n">${totalEmps}</div><div class="stat-l">Total Employees</div></div>
      <div class="stat"><div class="stat-n" style="color:var(--ok)">${presentSet.size}</div><div class="stat-l">Present Now</div></div>
      <div class="stat"><div class="stat-n" style="color:var(--err)">${absent}</div><div class="stat-l">Absent</div></div>
      <div class="stat"><div class="stat-n" style="color:var(--orange)">${rate}%</div><div class="stat-l">Attendance Rate</div></div>
      <div class="stat"><div class="stat-n" style="color:#ef4444">${lateCount}</div><div class="stat-l">Late Today</div></div>
      <div class="stat"><div class="stat-n" style="color:var(--ok)">${onTimeCount}</div><div class="stat-l">On Time</div></div>
    </div>
    <div class="two-col" style="gap:16px">
      <div class="card">
        <div class="card-lbl"><i class="ti ti-building"></i> Site-by-Site Breakdown</div>
        ${siteBreakdown.map(sb=>`
          <div style="display:flex;align-items:center;gap:10px;padding:8px 0;border-bottom:1px solid var(--border)">
            <span class="site-badge" style="min-width:70px;text-align:center">${esc(sb.site.code)}</span>
            <div style="flex:1">
              <div style="font-weight:500;color:var(--navy);font-size:12px">${esc(sb.site.name)}</div>
              <div style="display:flex;gap:4px;margin-top:3px">
                <div style="flex:${sb.present||0};height:6px;background:var(--ok);border-radius:3px 0 0 3px"></div>
                <div style="flex:${sb.absent||0};height:6px;background:#fee2e2;border-radius:0 3px 3px 0"></div>
              </div>
            </div>
            <div style="text-align:right;font-size:11px">
              <span style="color:var(--ok);font-weight:600">${sb.present}</span><span style="color:var(--gray)">/${sb.total}</span>
            </div>
          </div>
        `).join('')}
      </div>
      <div class="card">
        <div class="card-lbl"><i class="ti ti-users"></i> Currently Present</div>
        <div style="max-height:300px;overflow-y:auto">
          ${EMPS.filter(e=>{const r=RECS.filter(x=>x.empUid===e.uid);return r.length&&r[r.length-1].type==='in';}).map(e=>{
            const recs=RECS.filter(r=>r.empUid===e.uid);
            const fi=recs.find(r=>r.type==='in');
            const ts=fi?.timeStatus;
            return `<div style="display:flex;align-items:center;gap:8px;padding:6px 0;border-bottom:1px solid var(--border)">
              <div class="av" style="width:26px;height:26px;font-size:9px">${ini(e.name)}</div>
              <div style="flex:1">
                <div style="font-weight:500;color:var(--navy);font-size:12px">${esc(e.name)}</div>
                <div style="font-size:10px;color:var(--gray)">${esc(e.id)} · ${(e.sites||[]).map(id=>SITES.find(s=>s.id===id)?.code||id).join(', ')}</div>
              </div>
              ${ts?`<span class="${ts.cls}" style="font-size:9px">${ts.label}</span>`:'<span class="in-b" style="font-size:9px">IN</span>'}
              <span style="font-size:9px;color:var(--gray)">${fi?fi.time.toLocaleTimeString('en-US',{hour:'2-digit',minute:'2-digit'}):''}</span>
            </div>`;
          }).join('') || '<div class="empty" style="padding:20px"><i class="ti ti-users"></i> No one checked in yet</div>'}
        </div>
      </div>
    </div>`;
}

/* ── SUPERVISOR DASHBOARD STRIP ── */
function renderSupDash() {
  const el = document.getElementById('sup-dash'); if(!el||!sSite) return;
  const sEmps = EMPS.filter(e=>(e.sites||[]).includes(sSite.id));
  const presentSet = new Set();
  sEmps.forEach(e=>{
    const recs = RECS.filter(r=>r.empUid===e.uid&&r.siteId===sSite.id);
    const last = recs[recs.length-1];
    if(last&&last.type==='in') presentSet.add(e.uid);
  });
  el.innerHTML = `
    <div class="stats-row" style="grid-template-columns:repeat(3,1fr);margin-bottom:0">
      <div class="stat"><div class="stat-n" style="color:var(--ok)">${presentSet.size}</div><div class="stat-l">Present</div></div>
      <div class="stat"><div class="stat-n" style="color:var(--err)">${sEmps.length-presentSet.size}</div><div class="stat-l">Absent</div></div>
      <div class="stat"><div class="stat-n" style="color:var(--orange)">${sEmps.length}</div><div class="stat-l">Total</div></div>
    </div>`;
}

/* ── SETTINGS TAB ── */
function _setVal(id, val) { const el = document.getElementById(id); if (el) el.value = val; }
function _setTxt(id, val) { const el = document.getElementById(id); if (el) el.textContent = val; }
function _getVal(id, fallback) { const el = document.getElementById(id); return el ? el.value : fallback; }

function initSettings() {
  _setVal('set-company', SETTINGS.companyName);
  _setVal('set-short', SETTINGS.companyShort);
  _setVal('set-threshold', SETTINGS.faceThreshold);
  _setTxt('set-threshold-v', SETTINGS.faceThreshold);
  _setVal('set-scan-threshold', SETTINGS.scanThreshold);
  _setTxt('set-scan-threshold-v', SETTINGS.scanThreshold);
  _setVal('set-confirm-frames', SETTINGS.confirmFrames);
  _setVal('set-min-face', Math.round(SETTINGS.minFaceRatio * 100));
  _setTxt('set-min-face-v', Math.round(SETTINGS.minFaceRatio * 100) + '%');
  _setVal('set-cooldown', SETTINGS.cooldownFrames);
  _setVal('set-shift-start', SETTINGS.shiftStart);
  _setVal('set-shift-end', SETTINGS.shiftEnd);
  _setVal('set-grace', SETTINGS.lateGraceMinutes);
  _setVal('set-timeout', SETTINGS.sessionTimeoutMin);
}

function saveSettingsForm() {
  SETTINGS.companyName = _getVal('set-company', SETTINGS.companyName).trim() || SETTINGS.companyName;
  SETTINGS.companyShort = _getVal('set-short', SETTINGS.companyShort).trim() || SETTINGS.companyShort;
  SETTINGS.faceThreshold = parseFloat(_getVal('set-threshold', SETTINGS.faceThreshold));
  SETTINGS.scanThreshold = parseFloat(_getVal('set-scan-threshold', SETTINGS.scanThreshold));
  SETTINGS.confirmFrames = parseInt(_getVal('set-confirm-frames', SETTINGS.confirmFrames));
  SETTINGS.minFaceRatio = parseInt(_getVal('set-min-face', Math.round(SETTINGS.minFaceRatio*100))) / 100;
  SETTINGS.cooldownFrames = parseInt(_getVal('set-cooldown', SETTINGS.cooldownFrames));
  SETTINGS.shiftStart = _getVal('set-shift-start', SETTINGS.shiftStart);
  SETTINGS.shiftEnd = _getVal('set-shift-end', SETTINGS.shiftEnd);
  SETTINGS.lateGraceMinutes = parseInt(_getVal('set-grace', SETTINGS.lateGraceMinutes));
  SETTINGS.sessionTimeoutMin = parseInt(_getVal('set-timeout', SETTINGS.sessionTimeoutMin));
  saveSettings();
  toast('Settings saved successfully');
}

/* ══════════════════════════════════════════
   SUPERVISOR
══════════════════════════════════════════ */
function initSup(){
  const sup=curUser;
  document.getElementById('sup-portal-title').textContent=sup.role+' Portal';
  document.getElementById('s-rolebadge').innerHTML=`<i class="ti ti-hard-hat" style="font-size:10px"></i> ${sup.role}`;
  document.getElementById('s-footer-name').textContent=sup.name+' · '+sup.role;
  const stripHTML=`
    <div class="sup-strip-left">
      <div class="av sup lg">${ini(sup.name)}</div>
      <div>
        <div class="sup-strip-name">${sup.name}</div>
        <div class="sup-strip-sub">${sup.loginId} · ${sup.role}</div>
      </div>
    </div>
    <div class="sup-strip-right">
      <div class="sup-strip-sites">Assigned: ${sup.sites.map(id=>SITES.find(s=>s.id===id)?.code||id).join(', ')||'None'}</div>
      <div class="sup-strip-hint">You mark attendance on behalf of your site employees</div>
    </div>`;
  document.getElementById('sup-strip').innerHTML=stripHTML;
  document.getElementById('sup-strip2').innerHTML=stripHTML;
  document.getElementById('sup-strip-enr').innerHTML=stripHTML;
  const mySiteOpts='<option value="">— Select your active site —</option>'+
    SITES.filter(s=>sup.sites.includes(s.id)).map(s=>`<option value="${s.id}">${s.name} (${s.code})</option>`).join('');
  document.getElementById('s-site-sel').innerHTML=mySiteOpts;
  document.getElementById('b-site-sel').innerHTML=mySiteOpts.replace('— Select your active site —','— Select site for bulk marking —');
  sFaceOk=false;sGeoOk=false;selEmp=null;sSite=null;
}

function sTab(t){
  ['mark','enroll','bulk','log'].forEach(k=>document.getElementById('sp-'+k).classList.toggle('active',k===t));
  document.querySelectorAll('#scr-sup .stab').forEach((b,i)=>b.classList.toggle('active',['mark','enroll','bulk','log'][i]===t));
  if(t==='log')renderMyLog();
  if(t==='bulk')renderBList();
  if(t==='enroll')initEnroll();
}

function sSiteChange(){
  const id=document.getElementById('s-site-sel').value;
  sSite=SITES.find(s=>s.id===id)||null;
  sGeoOk=false;sGeoData=null;
  document.getElementById('s-geo-refresh').disabled=!sSite;
  document.getElementById('s-geo-panel').style.display=sSite?'block':'none';
  document.getElementById('s-start-prompt').style.display='none';
  document.getElementById('s-scanner-panel').style.display='none';
  
  const si=document.getElementById('s-site-info');
  if(sSite){
    si.style.display='block';
    si.innerHTML=`<i class="ti ti-info-circle"></i> Target: ${sSite.name} · ${sSite.radius}km zone · <span style="font-family:var(--mono)">${sSite.lat}, ${sSite.lng}</span>`;
    sGeo(); // Auto-trigger geo verification
  } else si.style.display='none';
  renderSupDash();
}

function sGeo(){
  if(!sSite){toast('Select a site first',false);return;}
  const stat=document.getElementById('s-gstat');
  stat.textContent='Acquiring GPS...'; stat.className='cv-m';
  document.getElementById('s-geo-msg').style.display='none';
  
  navigator.geolocation.getCurrentPosition(pos=>{
    sGeoData={lat:pos.coords.latitude,lng:pos.coords.longitude,acc:pos.coords.accuracy};
    processSGeo(false);
  },()=>{
    sGeoData={lat:sSite.lat+(Math.random()-.5)*0.001,lng:sSite.lng+(Math.random()-.5)*0.001,acc:10,demo:true};
    processSGeo(true);
  },{enableHighAccuracy:true,timeout:8000});
}

function processSGeo(demo){
  const dist=hav(sGeoData.lat,sGeoData.lng,sSite.lat,sSite.lng);
  sGeoOk=dist<=sSite.radius;
  
  document.getElementById('s-gstat').textContent=demo?'Simulated (Demo)':'Acquired';
  document.getElementById('s-gstat').className=demo?'cv-warn':'cv-ok';
  document.getElementById('s-gdist').textContent=`${dist.toFixed(2)} km from site center`;
  document.getElementById('s-gdist').className=sGeoOk?'cv-ok':'cv-err';
  document.getElementById('s-gzone').textContent=sGeoOk?'✓ INSIDE RADIUS':'✗ OUTSIDE RADIUS';
  document.getElementById('s-gzone').className=sGeoOk?'cv-ok':'cv-err';
  
  const msg=document.getElementById('s-geo-msg');
  msg.style.display='block';
  if(sGeoOk){
    msg.innerHTML='<i class="ti ti-circle-check"></i> Location Verified. Site access granted.';
    msg.style.background='rgba(34,197,94,0.1)'; msg.style.color='#16a34a';
    document.getElementById('s-start-prompt').style.display='block';
  } else {
    msg.innerHTML=`<i class="ti ti-alert-triangle"></i> Outside zone by ${(dist-sSite.radius).toFixed(2)}km. Move to ${sSite.code} to continue.`;
    msg.style.background='rgba(239,68,68,0.1)'; msg.style.color='#dc2626';
    document.getElementById('s-start-prompt').style.display='none';
  }
}

let sScannerActive = false;
let _scanHits = {}; // uid → consecutive matching frame count

async function startScanner() {
  await loadModels();
  sScannerActive = true;
  document.getElementById('s-start-prompt').style.display='none';
  document.getElementById('s-scanner-panel').style.display='block';
  
  try {
    const stream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: 'user', width: 640, height: 480 } });
    sCamStream = stream;
    document.getElementById('s-vid').srcObject = stream;
    sScannerLoop();
    sLog(`Scanner session started for ${sSite.code}`, '#3b82f6');
  } catch (e) {
    toast('Camera access denied', false);
    stopScanner();
  }
}

function stopScanner() {
  sScannerActive = false;
  if(sCamStream) {
    sCamStream.getTracks().forEach(t=>t.stop());
    sCamStream = null;
  }
  document.getElementById('s-scanner-panel').style.display='none';
  document.getElementById('s-start-prompt').style.display='block';
}

function sLog(msg, color = '#94a3b8') {
  const log = document.getElementById('s-session-log');
  const d = document.createElement('div');
  d.style.color = color;
  d.textContent = `[${new Date().toLocaleTimeString()}] ${msg}`;
  log.appendChild(d);
  log.scrollTop = log.scrollHeight;
}

async function sScannerLoop() {
  if (!sScannerActive || !sSite) return;
  const vid    = document.getElementById('s-vid');
  const canvas = document.getElementById('s-cnv');
  const status = document.getElementById('s-scan-status');

  // ── Python server path (preferred) ──────────────────────────────────
  if (pyServerOnline) {
    if (!vid.videoWidth) { setTimeout(sScannerLoop, 100); return; }
    try {
      const siteUids = EMPS.filter(e => e.faceReg && (e.sites||[]).includes(sSite.id)).map(e => e.uid);
      const res = await pyRecognize(vid, siteUids.length ? siteUids : null);

      const ctx = canvas.getContext('2d');
      canvas.width = vid.videoWidth; canvas.height = vid.videoHeight;
      ctx.clearRect(0, 0, canvas.width, canvas.height);

      if (!res.ok) { status.textContent = `⚠ Server error: ${res.error}`; }
      else if (!res.match) {
        const reason = res.reason || 'No face';
        status.textContent = reason === 'No face detected' || reason === 'No face encoding'
          ? '👁 No face visible — step into frame'
          : `Face detected — ${reason}`;
        identifyCooldown = Math.max(0, identifyCooldown - 1);
        _scanHits = {};
      } else {
        const emp  = EMPS.find(e => e.uid === res.match);
        const name = emp ? emp.name : res.match;
        const conf = res.confidence || 0;
        _scanHits[res.match] = (_scanHits[res.match] || 0) + 1;
        const hits = _scanHits[res.match];
        const need = SETTINGS.confirmFrames || 3;
        const locked = hits >= need;
        identifyCooldown = Math.max(0, identifyCooldown - 1);
        status.textContent = locked
          ? `✓ ${name}  ${conf}%  — marking attendance`
          : `Identifying ${name}  ${conf}%  [${Math.min(hits,need)}/${need}]`;
        if (locked && identifyCooldown === 0) {
          handleSIdentification(res.match, 1 - conf / 100);
          _scanHits = {};
        }
      }
    } catch(e) {
      pyServerOnline = false;
      status.textContent = '⚠ Server disconnected — switching to browser AI';
      _updatePyStatus();
    }
    if (sScannerActive) setTimeout(sScannerLoop, 200); // slower poll for Python (network overhead)
    return;
  }

  // ── Browser face-api.js fallback ────────────────────────────────────
  if (!modelsLoaded) {
    if (status) status.textContent = '⏳ Loading AI face models…';
    setTimeout(sScannerLoop, 300);
    return;
  }

  const ds = { width: vid.videoWidth || 640, height: vid.videoHeight || 480 };
  if (ds.width === 0) { setTimeout(sScannerLoop, 100); return; }
  faceapi.matchDimensions(canvas, ds);

  try {
    // Use TinyFaceDetector (fast, webcam-optimised) + full recognition pipeline
    const detector = tinyLoaded ? _tinyOpts() : _ssdOpts();
    const detections = await faceapi.detectAllFaces(vid, detector)
      .withFaceLandmarks().withFaceDescriptors();
    const resized = faceapi.resizeResults(detections, ds);
    const ctx = canvas.getContext('2d');
    ctx.clearRect(0, 0, canvas.width, canvas.height);

    if (detections.length === 0) {
      status.textContent = '👁 Scanning… no face visible — step into frame';
      identifyCooldown = Math.max(0, identifyCooldown - 1);
      Object.keys(_scanHits).forEach(k => { _scanHits[k] = Math.max(0, _scanHits[k] - 1); if (!_scanHits[k]) delete _scanHits[k]; });
    } else {
      const pool = EMPS.filter(e => e.faceReg && (e.descriptor || e.descriptors) && (e.sites||[]).includes(sSite.id));
      identifyCooldown = Math.max(0, identifyCooldown - 1);

      if (pool.length === 0) {
        status.textContent = `✔ Face detected — no enrolled employees for site ${sSite.code}`;
        resized.forEach(det => new faceapi.draw.DrawBox(det.detection.box, { label: 'Face ✔ — Not enrolled', boxColor: '#f59e0b' }).draw(canvas));
      } else {
        const labeled = pool.map(e => {
          const descs = (e.descriptors && e.descriptors.length)
            ? e.descriptors.map(d => new Float32Array(d))
            : [new Float32Array(e.descriptor)];
          return new faceapi.LabeledFaceDescriptors(e.uid, descs);
        });
        const matcher = new faceapi.FaceMatcher(labeled, SETTINGS.scanThreshold);
        const confirmNeeded = SETTINGS.confirmFrames || 3;
        let statusSet = false;

        resized.forEach(det => {
          const box = det.detection.box;
          const faceRatio = (box.width * box.height) / (ds.width * ds.height);
          if (faceRatio < (SETTINGS.minFaceRatio || 0.04)) {
            new faceapi.draw.DrawBox(box, { label: 'Move closer ↑', boxColor: '#f59e0b', lineWidth: 2 }).draw(canvas);
            if (!statusSet) { status.textContent = 'Face too small — step closer to camera'; statusSet = true; }
            return;
          }

          const match = matcher.findBestMatch(det.descriptor);
          const conf = Math.round((1 - match.distance) * 100);

          if (match.label !== 'unknown') {
            const emp = EMPS.find(e => e.uid === match.label);
            const empName = emp ? emp.name : match.label;
            _scanHits[match.label] = (_scanHits[match.label] || 0) + 1;
            const hits = _scanHits[match.label];
            const locked = hits >= confirmNeeded;
            const prog = Math.min(hits, confirmNeeded);
            Object.keys(_scanHits).forEach(k => { if (k !== match.label) { _scanHits[k] = Math.max(0, _scanHits[k] - 1); if (!_scanHits[k]) delete _scanHits[k]; } });
            const boxColor = locked ? '#22c55e' : '#f59e0b';
            const label = locked ? `✓ ${empName}  ${conf}%` : `${empName}  ${conf}%  [${prog}/${confirmNeeded}]`;
            new faceapi.draw.DrawBox(box, { label, boxColor, lineWidth: locked ? 3 : 2 }).draw(canvas);
            if (!statusSet) {
              status.textContent = locked ? `✓ Confirmed: ${empName} (${conf}%)` : `Identifying ${empName}… ${prog}/${confirmNeeded}`;
              statusSet = true;
            }
            if (locked && identifyCooldown === 0) { handleSIdentification(match.label, match.distance); _scanHits = {}; }
          } else {
            new faceapi.draw.DrawBox(box, { label: `Face ✔  Not matched  ${conf}%`, boxColor: '#ef4444', lineWidth: 2 }).draw(canvas);
            Object.keys(_scanHits).forEach(k => { _scanHits[k] = Math.max(0, _scanHits[k] - 1); if (!_scanHits[k]) delete _scanHits[k]; });
            if (!statusSet) { status.textContent = `Face detected but not recognised — re-enroll employee for better results`; statusSet = true; }
          }
        });
        if (!statusSet) status.textContent = `${detections.length} face(s) in frame`;
      }
    }
  } catch (e) {
    console.error('Scanner error:', e);
    status.textContent = '⚠ Detection error — check console';
  }
  if (sScannerActive) setTimeout(sScannerLoop, 100);
}

function handleSIdentification(uid, dist) {
  const emp = EMPS.find(e => e.uid === uid);
  if (!emp || !sSite) return;
  const conf = Math.round((1 - dist) * 100);
  sLog(`✓ Punched: ${emp.name} (${conf}%)`, '#22c55e');
  
  const card = document.getElementById('s-last-match');
  document.getElementById('s-match-name').textContent = emp.name;
  document.getElementById('s-match-id').textContent = emp.id;
  document.getElementById('s-match-av').textContent = ini(emp.name);
  card.style.display = 'block';
  
  pushRec(emp, sSite, 'scanner-session');
  renderAStats();
  toast(`${emp.name} Marked`, true);
  
  identifyCooldown = SETTINGS.cooldownFrames;
  setTimeout(() => { card.style.display = 'none'; }, 4000);
}

/* ══════════════════════════════════════════
   FACE API LOGIC
══════════════════════════════════════════ */
let modelsLoaded = false;       // all models ready for recognition
let tinyLoaded   = false;       // TinyFaceDetector ready (faster, real-time)
let _modelPromise = null;

const MODEL_URL          = 'https://cdn.jsdelivr.net/npm/@vladmandic/face-api/model';
const MODEL_URL_FALLBACK = 'https://unpkg.com/@vladmandic/face-api/model';

function _setModelStatus(text, color) {
  document.querySelectorAll('.ai-status-lbl').forEach(el => {
    el.textContent = text;
    el.style.color = color || 'inherit';
  });
}

async function _tryLoadFrom(url) {
  await Promise.all([
    faceapi.nets.tinyFaceDetector.loadFromUri(url),
    faceapi.nets.faceLandmark68Net.loadFromUri(url),
    faceapi.nets.faceRecognitionNet.loadFromUri(url),
    faceapi.nets.ssdMobilenetv1.loadFromUri(url),
  ]);
}

async function loadModels() {
  if (modelsLoaded) return;
  if (_modelPromise) return _modelPromise;

  _modelPromise = (async () => {
    _setModelStatus('⏳ Loading AI…', '#f59e0b');
    try {
      await _tryLoadFrom(MODEL_URL);
    } catch (e1) {
      console.warn('Primary CDN failed, trying fallback…', e1);
      try {
        await _tryLoadFrom(MODEL_URL_FALLBACK);
      } catch (e2) {
        _modelPromise = null;
        _setModelStatus('✗ AI Failed — click Retry AI', '#ef4444');
        console.error('Both CDNs failed:', e2);
        toast('AI models failed — check internet, then click Retry AI', false);
        throw e2;
      }
    }
    modelsLoaded = true;
    tinyLoaded   = true;
    _setModelStatus('✓ AI Ready', '#22c55e');
    toast('Face AI ready', true);
  })();
  return _modelPromise;
}

async function retryModels() {
  _modelPromise = null;
  modelsLoaded  = false;
  tinyLoaded    = false;
  await loadModels();
}

// helper: get the best detector options (prefer Tiny for real-time)
function _tinyOpts() {
  return new faceapi.TinyFaceDetectorOptions({ scoreThreshold: 0.2, inputSize: 320 });
}
function _ssdOpts()  {
  return new faceapi.SsdMobilenetv1Options({ minConfidence: 0.3 });
}

// Start loading immediately on page open
loadModels();

/* ─── Quick camera test — shows raw detection result without needing enrollment ─── */
let _qtStream = null;
async function quickFaceTest() {
  const result = document.getElementById('quick-test-result');
  if (!result) { toast('Test only available on the Attendance page', false); return; }

  result.style.display = 'block';
  result.innerHTML = '⏳ Opening camera for test…';

  try {
    if (_qtStream) { _qtStream.getTracks().forEach(t => t.stop()); _qtStream = null; }
    _qtStream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: 'user', width: 640, height: 480 } });
  } catch(e) {
    result.innerHTML = '❌ Camera access denied — allow camera in browser then retry'; return;
  }

  const vid = document.createElement('video');
  vid.srcObject = _qtStream; vid.autoplay = true; vid.muted = true; vid.playsInline = true;

  result.innerHTML = '⏳ Warming up camera…';
  await new Promise(r => { vid.onloadeddata = r; setTimeout(r, 2000); });

  if (!modelsLoaded) {
    result.innerHTML = '⏳ Loading AI models… (this can take 5-15s on first load)';
    try { await loadModels(); } catch(e) {
      result.innerHTML = '❌ AI models failed to load — check internet connection and click <strong>Retry AI</strong>';
      _qtStream.getTracks().forEach(t => t.stop()); _qtStream = null; return;
    }
  }

  result.innerHTML = '🔍 Detecting face…';

  try {
    // Try TinyFaceDetector first
    let det = await faceapi.detectSingleFace(vid, _tinyOpts()).withFaceLandmarks().withFaceDescriptor();
    if (!det) {
      // Fall back to SSD with very low threshold
      det = await faceapi.detectSingleFace(vid, new faceapi.SsdMobilenetv1Options({ minConfidence: 0.2 })).withFaceLandmarks().withFaceDescriptor();
    }

    if (det) {
      const score = Math.round(det.detection.score * 100);
      const pts = det.landmarks.positions.length;
      const descLen = det.descriptor.length;
      result.innerHTML = `✅ <strong style="color:#22c55e">FACE DETECTED</strong> — Confidence: ${score}% · Landmarks: ${pts} pts · Descriptor: ${descLen}D vector<br>
        <span style="color:#94a3b8;font-size:10px">Detection is working correctly. If attendance scan still fails, the issue is with face matching (not detection). Re-enroll the employee with better lighting.</span>`;
    } else {
      result.innerHTML = `⚠️ <strong style="color:#f59e0b">NO FACE FOUND</strong><br>
        <span style="color:#94a3b8;font-size:10px">Camera is working but face was not detected. Try: face camera directly · improve lighting · move closer · ensure face is fully visible.</span>`;
    }
  } catch(e) {
    result.innerHTML = `❌ <strong style="color:#ef4444">DETECTION ERROR</strong>: ${e.message}<br>
      <span style="color:#94a3b8;font-size:10px">This usually means models aren't loaded. Click <strong>Retry AI</strong> and wait for "✓ AI Ready" then test again.</span>`;
    console.error('quickFaceTest error:', e);
  }

  _qtStream.getTracks().forEach(t => t.stop()); _qtStream = null;
}

function initEnroll() {
  const pool = curRole === 'admin' ? EMPS : EMPS.filter(e => (e.sites || []).some(sid => curUser.sites.includes(sid)));
  const opts = '<option value="">— Choose employee —</option>' + pool.map(e => `<option value="${e.uid}">${e.name} (${e.id})</option>`).join('');
  if (curRole === 'admin') document.getElementById('enr-emp-sel').innerHTML = opts;
  else document.getElementById('se-emp-sel').innerHTML = opts;
}

let enrStream = null, enrTarget = null;
async function startEnrCam() {
  await loadModels();
  const vid = curRole === 'admin' ? document.getElementById('enr-vid') : document.getElementById('se-vid');
  try {
    enrStream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: 'user', width: 640, height: 480 } });
    vid.srcObject = enrStream;
    if (curRole === 'admin') document.getElementById('enr-cap-btn').disabled = !enrTarget;
    else document.getElementById('se-cap-btn').disabled = !enrTarget;
  } catch (e) { toast('Camera access denied', false); }
}

async function startSeCam() {
  await loadModels();
  const vid = document.getElementById('se-vid');
  try {
    enrStream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: 'user', width: 640, height: 480 } });
    vid.srcObject = enrStream;
    document.getElementById('se-cap-btn').disabled = !enrTarget;
  } catch (e) { toast('Camera access denied', false); }
}

function sCheckReady() {
  const markBtn = document.getElementById('s-mark-btn');
  const scanBtn = document.getElementById('s-scan-btn');
  if (markBtn) markBtn.disabled = !selEmp || !sFaceOk || !sGeoOk || scanning;
  if (scanBtn) scanBtn.disabled = !selEmp || !sCamStream || scanning;
}

function enrEmpChange() {
  const uid = document.getElementById('enr-emp-sel').value;
  enrTarget = EMPS.find(e => e.uid === uid);
  const info = document.getElementById('enr-emp-info');
  if (enrTarget) {
    info.innerHTML = `<div class="emp-profile"><div class="av lg">${ini(enrTarget.name)}</div><div class="info"><div class="ep-name">${enrTarget.name}</div><div class="ep-sub">${enrTarget.id} · ${enrTarget.dept}</div><div class="ep-face">${enrTarget.faceReg ? '<span class="face-pill-ok">Enrolled</span>' : '<span class="face-pill-no">Not Enrolled</span>'}</div></div></div>`;
    document.getElementById('enr-cap-btn').disabled = !enrStream;
  } else info.innerHTML = '';
}

function seEmpChange() {
  const uid = document.getElementById('se-emp-sel').value;
  enrTarget = EMPS.find(e => e.uid === uid);
  const info = document.getElementById('se-emp-info');
  if (enrTarget) {
    info.innerHTML = `<div class="emp-profile"><div class="av lg">${ini(enrTarget.name)}</div><div class="info"><div class="ep-name">${enrTarget.name}</div><div class="ep-sub">${enrTarget.id} · ${enrTarget.dept}</div><div class="ep-face">${enrTarget.faceReg ? '<span class="face-pill-ok">Enrolled</span>' : '<span class="face-pill-no">Not Enrolled</span>'}</div></div></div>`;
    document.getElementById('se-cap-btn').disabled = !enrStream;
  } else info.innerHTML = '';
}

async function captureEnroll() { captureSeEnroll(); } // Proxy for admin

let enrollSamples = [];
const ENR_SAMPLES = 3;

async function captureSeEnroll() {
  if (!enrTarget || !modelsLoaded) return;
  const vid = curRole === 'admin' ? document.getElementById('enr-vid') : document.getElementById('se-vid');
  const stat = curRole === 'admin' ? document.getElementById('enr-status') : document.getElementById('se-status');
  const capBtn = curRole==='admin' ? document.getElementById('enr-cap-btn') : document.getElementById('se-cap-btn');

  stat.textContent = `Scanning... capturing sample ${enrollSamples.length+1}/${ENR_SAMPLES}`;
  stat.style.color = 'var(--orange)';
  capBtn.disabled = true;

  try {
    // Try Tiny first (fast), fall back to SSD
    let detection = await faceapi.detectSingleFace(vid, _tinyOpts()).withFaceLandmarks().withFaceDescriptor();
    if (!detection) detection = await faceapi.detectSingleFace(vid, _ssdOpts()).withFaceLandmarks().withFaceDescriptor();
    if (!detection) {
      stat.textContent = '✗ No face detected. Face the camera directly in good light.';
      stat.style.color = 'var(--err)';
      capBtn.disabled = false;
      return;
    }
    enrollSamples.push(Array.from(detection.descriptor));
    stat.textContent = `✓ Sample ${enrollSamples.length}/${ENR_SAMPLES} captured — ${ENR_SAMPLES - enrollSamples.length > 0 ? 'click again to capture next' : 'processing...'}`;
    stat.style.color = enrollSamples.length < ENR_SAMPLES ? 'var(--orange)' : 'var(--ok)';

    if (enrollSamples.length >= ENR_SAMPLES) {
      // Average descriptors for better accuracy
      const avg = new Array(128).fill(0);
      for (const d of enrollSamples) { for (let i=0;i<128;i++) avg[i]+=d[i]; }
      for (let i=0;i<128;i++) avg[i]/=ENR_SAMPLES;

      // Capture photo (160x160)
      const photoCanvas = document.createElement('canvas');
      photoCanvas.width = 160; photoCanvas.height = 160;
      const pCtx = photoCanvas.getContext('2d');
      pCtx.scale(-1,1); pCtx.drawImage(vid,-160,0,160,160); // mirror to match mirrored video
      enrTarget.photo = photoCanvas.toDataURL('image/jpeg', 0.75);

      enrTarget.descriptor = avg;
      enrTarget.descriptors = enrollSamples.slice();
      enrTarget.faceReg = true;
      enrTarget.enrolledAt = new Date().toISOString();
      enrTarget.enrolledBy = curUser?.name || 'Admin';
      enrollSamples = [];

      // Also sync to Python server if running
      if (pyServerOnline) {
        try {
          await pyEnroll(enrTarget.uid, enrTarget.name, vid);
          stat.textContent = `✓ Enrolled — browser + Python server synced`;
        } catch(e) { stat.textContent = `✓ Enrolled (browser only — Python server sync failed)`; }
      } else {
        stat.textContent = `✓ Enrolled with ${ENR_SAMPLES}-sample biometric average`;
      }

      stat.style.color = 'var(--ok)';
      capBtn.disabled = false;
      toast(`Enrolled: ${enrTarget.name}`);
      saveData();
      if(curRole==='admin') enrEmpChange(); else seEmpChange();
    } else {
      capBtn.disabled = false;
    }
  } catch(e) {
    stat.textContent = '✗ Analysis error — try again';
    stat.style.color = 'var(--err)';
    console.error(e);
    capBtn.disabled = false;
  }
}

/* ══════════════════════════════════════════
   TERMINAL MODE LOGIC
══════════════════════════════════════════ */
let termActive = false;
let termLoop = null;
let lastIdentifiedId = null;
let identifyCooldown = 0;

function initTerm() {
  const sel = document.getElementById('term-site-sel');
  sel.innerHTML = '<option value="">— Select Site for this Terminal —</option>' + SITES.map(s => `<option value="${s.id}">${s.name} (${s.code})</option>`).join('');
  
  setInterval(() => {
    const el = document.getElementById('term-clock');
    if (el) el.textContent = new Date().toLocaleTimeString();
  }, 1000);
}

function termLog(msg, color = '#94a3b8') {
  const log = document.getElementById('term-log');
  const d = document.createElement('div');
  d.style.color = color;
  d.textContent = `[${new Date().toLocaleTimeString()}] ${msg}`;
  log.appendChild(d);
  log.scrollTop = log.scrollHeight;
}

async function startTerminal() {
  const siteId = document.getElementById('term-site-sel').value;
  if (!siteId) { toast('Please select a site for the terminal', false); return; }
  
  await loadModels();
  termActive = true;
  document.getElementById('term-start-btn').style.display = 'none';
  document.getElementById('term-stop-btn').style.display = 'block';
  document.getElementById('term-status').textContent = 'Camera Warming Up...';
  
  try {
    const stream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: 'user', width: 640, height: 480 } });
    const vid = document.getElementById('term-vid');
    vid.srcObject = stream;
    termLog('Camera active. Scanning for faces...', '#3b82f6');
    
    // Start recognition loop
    terminalLoop();
  } catch (e) {
    termLog('Camera access denied.', '#ef4444');
    stopTerminal();
  }
}

function stopTerminal() {
  termActive = false;
  document.getElementById('term-start-btn').style.display = 'block';
  document.getElementById('term-stop-btn').style.display = 'none';
  const vid = document.getElementById('term-vid');
  if (vid.srcObject) {
    vid.srcObject.getTracks().forEach(t => t.stop());
    vid.srcObject = null;
  }
  document.getElementById('term-status').textContent = 'Terminal Offline';
  termLog('Terminal stopped.', '#94a3b8');
}

let _termHits = {}; // uid → consecutive matching frame count

async function terminalLoop() {
  if (!termActive) return;

  // Wait for models if still loading
  if (!modelsLoaded) {
    const st = document.getElementById('term-status');
    if (st) st.textContent = '⏳ Loading AI models…';
    setTimeout(terminalLoop, 300);
    return;
  }

  const vid = document.getElementById('term-vid');
  const canvas = document.getElementById('term-cnv');
  const status = document.getElementById('term-status');
  const ds = { width: vid.videoWidth || 640, height: vid.videoHeight || 480 };

  if (ds.width === 0) { setTimeout(terminalLoop, 100); return; }

  faceapi.matchDimensions(canvas, ds);

  try {
    const detector = tinyLoaded ? _tinyOpts() : _ssdOpts();
    const detections = await faceapi.detectAllFaces(vid, detector)
      .withFaceLandmarks().withFaceDescriptors();
    const resized = faceapi.resizeResults(detections, ds);
    const ctx = canvas.getContext('2d');
    ctx.clearRect(0, 0, canvas.width, canvas.height);

    if (detections.length === 0) {
      status.textContent = '👁 Scanning… no face visible';
      identifyCooldown = Math.max(0, identifyCooldown - 1);
      Object.keys(_termHits).forEach(k => { _termHits[k] = Math.max(0, _termHits[k] - 1); if (!_termHits[k]) delete _termHits[k]; });
    } else {
      const pool = EMPS.filter(e => e.faceReg && (e.descriptor || e.descriptors));
      identifyCooldown = Math.max(0, identifyCooldown - 1);

      if (pool.length === 0) {
        status.textContent = 'Face detected — no enrolled employees';
        resized.forEach(det => new faceapi.draw.DrawBox(det.detection.box, { label: 'No enrolled staff', boxColor: '#f59e0b' }).draw(canvas));
      } else {
        const labeled = pool.map(e => {
          const descs = (e.descriptors && e.descriptors.length)
            ? e.descriptors.map(d => new Float32Array(d))
            : [new Float32Array(e.descriptor)];
          return new faceapi.LabeledFaceDescriptors(e.uid, descs);
        });
        const matcher = new faceapi.FaceMatcher(labeled, SETTINGS.scanThreshold);
        const confirmNeeded = SETTINGS.confirmFrames || 3;
        let statusSet = false;

        resized.forEach(det => {
          const box = det.detection.box;
          const faceRatio = (box.width * box.height) / (ds.width * ds.height);
          if (faceRatio < (SETTINGS.minFaceRatio || 0.04)) {
            new faceapi.draw.DrawBox(box, { label: 'Move closer ↑', boxColor: '#f59e0b', lineWidth: 2 }).draw(canvas);
            if (!statusSet) { status.textContent = 'Face too far — step closer to camera'; statusSet = true; }
            return;
          }

          const match = matcher.findBestMatch(det.descriptor);
          const conf = Math.round((1 - match.distance) * 100);

          if (match.label !== 'unknown') {
            const emp = EMPS.find(e => e.uid === match.label);
            const empName = emp ? emp.name : match.label;
            _termHits[match.label] = (_termHits[match.label] || 0) + 1;
            const hits = _termHits[match.label];
            const locked = hits >= confirmNeeded;
            const prog = Math.min(hits, confirmNeeded);
            Object.keys(_termHits).forEach(k => { if (k !== match.label) { _termHits[k] = Math.max(0, _termHits[k] - 1); if (!_termHits[k]) delete _termHits[k]; } });

            const boxColor = locked ? '#22c55e' : '#f59e0b';
            const lw = locked ? 3 : 2;
            const label = locked ? `✓ ${empName}  ${conf}%` : `${empName}  ${conf}%  [${prog}/${confirmNeeded}]`;
            new faceapi.draw.DrawBox(box, { label, boxColor, lineWidth: lw }).draw(canvas);

            if (!statusSet) {
              status.textContent = locked ? `✓ Confirmed: ${empName} (${conf}% match)` : `Identifying ${empName}… ${prog}/${confirmNeeded} frames`;
              statusSet = true;
            }
            if (locked && identifyCooldown === 0) {
              handleIdentification(match.label, match.distance);
              _termHits = {};
            }
          } else {
            new faceapi.draw.DrawBox(box, { label: `Unknown  ${conf}%`, boxColor: '#ef4444', lineWidth: 2 }).draw(canvas);
            Object.keys(_termHits).forEach(k => { _termHits[k] = Math.max(0, _termHits[k] - 1); if (!_termHits[k]) delete _termHits[k]; });
            if (!statusSet) { status.textContent = 'Face not recognised — ensure employee is enrolled'; statusSet = true; }
          }
        });
        if (!statusSet) status.textContent = `${detections.length} face(s) detected`;
      }
    }
  } catch (e) { console.error(e); }

  if (termActive) setTimeout(terminalLoop, 100);
}

function getEAR(eye) {
  const p2p6 = dist(eye[1], eye[5]);
  const p3p5 = dist(eye[2], eye[4]);
  const p1p4 = dist(eye[0], eye[3]);
  return (p2p6 + p3p5) / (2.0 * p1p4);
}
function dist(a, b) { return Math.sqrt((a.x - b.x)**2 + (a.y - b.y)**2); }

/* ── LATE / EARLY DETECTION ── */
function getTimeStatus(checkInTime) {
  if (!SETTINGS.shiftStart) return { label: 'IN', cls: 'in-b' };
  const [sh, sm] = SETTINGS.shiftStart.split(':').map(Number);
  const deadline = new Date(checkInTime);
  deadline.setHours(sh, sm + SETTINGS.lateGraceMinutes, 0, 0);
  const shiftExact = new Date(checkInTime);
  shiftExact.setHours(sh, sm, 0, 0);
  if (checkInTime <= shiftExact) return { label: 'EARLY', cls: 'early-b' };
  if (checkInTime <= deadline) return { label: 'ON TIME', cls: 'in-b' };
  const minsLate = Math.round((checkInTime - deadline) / 60000);
  return { label: `LATE +${minsLate}m`, cls: 'late-b' };
}

function handleIdentification(uid, dist) {
  const emp = EMPS.find(e => e.uid === uid);
  const siteId = document.getElementById('term-site-sel').value;
  const site = SITES.find(s => s.id === siteId);
  
  if (!emp || !site) return;
  
  // Logic: Mark attendance if it's a new match or after a short cooldown
  const conf = Math.round((1 - dist) * 100);
  termLog(`Recognized: ${emp.name} (${conf}%)`, '#22c55e');
  
  // Show UI match card
  const card = document.getElementById('term-match-card');
  document.getElementById('term-match-name').textContent = emp.name;
  document.getElementById('term-match-id').textContent = emp.id;
  document.getElementById('term-match-av').textContent = ini(emp.name);
  card.style.display = 'block';
  
  // Push record
  pushRec(emp, site, 'kiosk-autoid');
  renderAStats();
  toast(`Verified: ${emp.name}`, true);
  
  identifyCooldown = SETTINGS.cooldownFrames; // Delay next ID to avoid double-punches
  setTimeout(() => { card.style.display = 'none'; }, 5000);
}

async function sScan(){
  if(!selEmp||!sCamStream)return;
  scanning=true; sCheckReady();
  const v = document.getElementById('s-vid');

  // ── Python server path ──────────────────────────────────────────────
  if (pyServerOnline) {
    document.getElementById('s-cam-lbl').textContent = 'Python AI verifying…';
    document.getElementById('s-fchk').textContent = 'Analysing…';
    try {
      const res = await pyVerify(selEmp.uid, v);
      const conf = res.confidence || Math.round((1 - (res.distance||1)) * 100);
      document.getElementById('s-score-wrap').style.display = 'block';
      document.getElementById('s-sfill').style.width = Math.max(conf,0)+'%';
      document.getElementById('s-sfill').style.background = res.match ? 'var(--ok)' : 'var(--err)';
      document.getElementById('s-stxt').textContent = conf+'%';
      if (res.match) {
        sFaceOk = true;
        document.getElementById('s-fchk').textContent = `✓ Verified (${conf}%)`; document.getElementById('s-fchk').className = 'cv-ok';
        document.getElementById('s-result').innerHTML = `✓ Employee verified via Python AI — ${conf}% confidence`;
        document.getElementById('s-result').className = 'rbox rb-ok';
        document.getElementById('s-cam-lbl').textContent = `✓ Verified: ${selEmp.name}`;
      } else {
        sFaceOk = false;
        const reason = res.reason || 'No face detected';
        document.getElementById('s-fchk').textContent = `✗ ${reason} (${conf}%)`; document.getElementById('s-fchk').className = 'cv-err';
        document.getElementById('s-result').innerHTML = `✗ ${reason} — ${conf}% similarity.<br><small style="opacity:.7">Ensure good lighting. Use Manual Override if needed.</small>`;
        document.getElementById('s-result').className = 'rbox rb-err';
      }
    } catch(e) {
      sFaceOk = false;
      document.getElementById('s-fchk').textContent = '✗ Server error'; document.getElementById('s-fchk').className = 'cv-err';
      document.getElementById('s-result').textContent = 'Python server error — try again or use Manual Override';
      document.getElementById('s-result').className = 'rbox rb-err';
    }
    scanning = false; sCheckReady();
    return;
  }

  // ── Browser face-api.js fallback ────────────────────────────────────
  if(!modelsLoaded){
    document.getElementById('s-fchk').textContent='Loading AI…';
    try { await loadModels(); } catch(e) {
      toast('AI models not ready — check internet and retry', false);
      scanning=false; sCheckReady();
      return;
    }
  }

  // Wait for video feed to have actual frames
  if(!v.videoWidth || v.readyState < 2) {
    document.getElementById('s-cam-lbl').textContent = 'Warming up camera…';
    await new Promise(r => {
      const onReady = () => r();
      v.addEventListener('loadeddata', onReady, { once: true });
      setTimeout(onReady, 2500); // max wait 2.5s
    });
  }

  document.getElementById('s-cam-lbl').textContent = 'Detecting face…';
  document.getElementById('s-fchk').textContent = 'Scanning…';

  try{
    // TinyFaceDetector first (fast), fall back to SSD if needed
    const detection = await faceapi
      .detectSingleFace(v, tinyLoaded ? _tinyOpts() : _ssdOpts())
      .withFaceLandmarks()
      .withFaceDescriptor();

    if(detection){
      if(selEmp.faceReg && (selEmp.descriptor || selEmp.descriptors)){
        // Use all enrolled descriptors if available, else fall back to single averaged
        let bestDist = Infinity;
        const descs = (selEmp.descriptors && selEmp.descriptors.length)
          ? selEmp.descriptors.map(d => new Float32Array(d))
          : [new Float32Array(selEmp.descriptor)];
        descs.forEach(d => {
          const dist = faceapi.euclideanDistance(d, detection.descriptor);
          if(dist < bestDist) bestDist = dist;
        });

        const match = bestDist < SETTINGS.faceThreshold;
        const conf = Math.round((1 - bestDist) * 100);
        document.getElementById('s-score-wrap').style.display='block';
        document.getElementById('s-sfill').style.width = Math.max(conf,0)+'%';
        document.getElementById('s-sfill').style.background = match ? 'var(--ok)' : 'var(--err)';
        document.getElementById('s-stxt').textContent = conf+'%';

        if(match){
          sFaceOk=true;
          document.getElementById('s-fchk').textContent=`✓ Verified (${conf}%)`; document.getElementById('s-fchk').className='cv-ok';
          document.getElementById('s-result').innerHTML=`✓ Employee verified via Biometrics — ${conf}% confidence`;
          document.getElementById('s-result').className='rbox rb-ok';
          document.getElementById('s-cam-lbl').textContent=`✓ Verified: ${selEmp.name}`;
        } else {
          sFaceOk=false;
          document.getElementById('s-fchk').textContent=`✗ Mismatch (${conf}%)`; document.getElementById('s-fchk').className='cv-err';
          document.getElementById('s-result').innerHTML=`✗ Face does not match — ${conf}% similarity (need >${Math.round((1-SETTINGS.faceThreshold)*100)}%).<br><small style="opacity:.7">Ensure good lighting and face the camera directly. Use Manual Override if needed.</small>`;
          document.getElementById('s-result').className='rbox rb-err';
        }
      } else {
        // Employee has no face enrolled — allow override
        sFaceOk=true;
        document.getElementById('s-fchk').textContent='✓ Face detected'; document.getElementById('s-fchk').className='cv-ok';
        document.getElementById('s-result').textContent='✓ Face detected — employee not enrolled (manual override allowed)';
        document.getElementById('s-result').className='rbox rb-ok';
      }
    } else {
      sFaceOk=false;
      document.getElementById('s-fchk').textContent='✗ No face detected'; document.getElementById('s-fchk').className='cv-err';
      document.getElementById('s-result').innerHTML='✗ No face detected — face the camera directly in good light.<br><small style="opacity:.7">Click <b>Test Detection</b> above to diagnose. Or use Manual Override.</small>';
      document.getElementById('s-result').className='rbox rb-err';
      document.getElementById('s-cam-lbl').textContent='No face found';
    }
  }catch(e){
    console.error('sScan error:', e);
    sFaceOk=false;
    document.getElementById('s-fchk').textContent='✗ AI error'; document.getElementById('s-fchk').className='cv-err';
    document.getElementById('s-result').textContent='AI processing error — models may not be fully loaded. Wait a moment and retry.';
    document.getElementById('s-result').className='rbox rb-err';
    toast('Face scan error — try again', false);
  }
  scanning=false; sCheckReady();
}

function pushRec(emp,site,method){
  const now=new Date();
  const sr=RECS.filter(r=>r.empUid===emp.uid&&r.siteId===site.id);
  const lastIn=sr.filter(r=>r.type==='in').pop();
  const lastOut=sr.filter(r=>r.type==='out').pop();
  const type=(!lastIn||(lastOut&&lastOut.time>lastIn.time))?'in':'out';
  const timeStatus = type==='in' ? getTimeStatus(now) : null;
  RECS.push({
    empUid:emp.uid,empName:emp.name,empId:emp.id,dept:emp.dept,
    siteId:site.id,siteName:site.name,siteCode:site.code,
    type,time:now,lat:sGeoData?.lat,lng:sGeoData?.lng,
    markedBy:curUser.uid,markedByName:curUser.name,markedByRole:curUser.role,
    method,timeStatus
  });
  return type;
}

function sMark(){
  if(!selEmp||!sSite||!sFaceOk||!sGeoOk)return;
  const type=pushRec(selEmp,sSite,'face+geo');
  document.getElementById('s-result').innerHTML=`✓ ${type==='in'?'CHECKED IN':'CHECKED OUT'} — ${selEmp.name}<br><span style="font-size:10px;opacity:.8">Face verified · ${new Date().toLocaleTimeString('en-US',{hour:'2-digit',minute:'2-digit'})} · ${sSite.name}</span>`;
  document.getElementById('s-result').className='rbox rb-ok';
  sFaceOk=false;
  document.getElementById('s-fchk').textContent='—';document.getElementById('s-fchk').className='cv-m';
  document.getElementById('s-score-wrap').style.display='none';
  document.getElementById('s-mark-btn').disabled=true;
  renderAStats();renderMyLog();
  toast(`${type==='in'?'Checked in':'Checked out'}: ${selEmp.name} @ ${sSite.code}`);
}

function sManual(){
  if(!selEmp||!sSite||!sGeoOk)return;
  if(!confirm(`Mark attendance for ${selEmp.name} WITHOUT face scan?\n\nThis will be logged as manual override by ${curUser.name} (${curUser.role}).`))return;
  const type=pushRec(selEmp,sSite,'manual-override');
  document.getElementById('s-result').innerHTML=`✓ ${type==='in'?'CHECKED IN':'CHECKED OUT'} (Manual Override) — ${selEmp.name}<br><span style="font-size:10px;opacity:.8">No face scan · Override logged · ${new Date().toLocaleTimeString('en-US',{hour:'2-digit',minute:'2-digit'})}</span>`;
  document.getElementById('s-result').className='rbox rb-ok';
  renderAStats();renderMyLog();
  toast(`Manual override: ${selEmp.name} ${type.toUpperCase()} @ ${sSite.code}`);
  sCheckReady();
}

/* ── BULK ── */
function bSiteChange(){
  const id=document.getElementById('b-site-sel').value;
  bulkSite=SITES.find(s=>s.id===id)||null;
  renderBList();
}

function renderBList(){
  const el=document.getElementById('b-list');
  if(!bulkSite){el.innerHTML='<div class="empty"><i class="ti ti-building"></i>Select a site</div>';document.getElementById('b-count').textContent='0 employees';return;}
  const emps=EMPS.filter(e=>(e.sites||[]).includes(bulkSite.id));
  document.getElementById('b-count').textContent=`${emps.length} employee${emps.length!==1?'s':''}`;
  if(!emps.length){el.innerHTML='<div class="empty"><i class="ti ti-users"></i>No employees assigned to this site</div>';return;}
  el.innerHTML=emps.map(e=>{
    const recs=RECS.filter(r=>r.empUid===e.uid&&r.siteId===bulkSite.id);
    const marked=recs.length>0;const isIn=marked&&recs[recs.length-1].type==='in';
    return `<div class="emp-q ${marked?(isIn?'done-in':'done-out'):''}" id="bq-${esc(e.uid)}" onclick="${marked?'':'bMarkOne(\''+esc(e.uid)+'\')'}">
      <div class="av" style="width:26px;height:26px;font-size:9px">${ini(e.name)}</div>
      <div style="flex:1"><div class="eq-name">${esc(e.name)}</div><div class="eq-sub">${esc(e.id)} &nbsp;·&nbsp; ${esc(e.dept)} ${e.faceReg?'&nbsp;·&nbsp; <span style="color:var(--ok);font-size:9px">Face ✓</span>':''}</div></div>
      <div style="font-size:10px;font-weight:500;color:${marked?(isIn?'var(--ok)':'var(--gray)'):''}">${marked?(isIn?'✓ IN':'✓ OUT'):'Tap → mark IN'}</div>
    </div>`;
  }).join('');
}

function bMarkOne(uid){
  const emp=EMPS.find(e=>e.uid===uid);if(!emp||!bulkSite)return;
  const now=new Date();
  RECS.push({empUid:emp.uid,empName:emp.name,empId:emp.id,dept:emp.dept,siteId:bulkSite.id,siteName:bulkSite.name,siteCode:bulkSite.code,type:'in',time:now,markedBy:curUser.uid,markedByName:curUser.name,markedByRole:curUser.role,method:'bulk-single'});
  renderBList();renderBLog();renderAStats();
  toast(`${emp.name} marked IN`);
}

function bMarkAll(type){
  if(!bulkSite)return;
  if(!confirm(`Mark ALL employees at ${bulkSite.name} as ${type.toUpperCase()}?`))return;
  const now=new Date();
  EMPS.filter(e=>(e.sites||[]).includes(bulkSite.id)).forEach(e=>{
    const r=RECS.filter(x=>x.empUid===e.uid&&x.siteId===bulkSite.id);
    if(!r.length||r[r.length-1].type!==type)
      RECS.push({empUid:e.uid,empName:e.name,empId:e.id,dept:e.dept,siteId:bulkSite.id,siteName:bulkSite.name,siteCode:bulkSite.code,type,time:now,markedBy:curUser.uid,markedByName:curUser.name,markedByRole:curUser.role,method:'bulk-all'});
  });
  renderBList();renderBLog();renderAStats();
  toast(`All employees marked ${type.toUpperCase()} at ${bulkSite.name}`);
}

function bClear(){
  if(!bulkSite||!confirm('Clear all attendance records for this site today?'))return;
  RECS=RECS.filter(r=>r.siteId!==bulkSite.id);
  renderBList();renderBLog();renderAStats();toast('Records cleared',false);
}

function renderBLog(){
  const el=document.getElementById('b-log');
  const recs=RECS.filter(r=>r.markedBy===curUser.uid);
  if(!recs.length){el.innerHTML='<div class="empty"><i class="ti ti-inbox"></i>No records yet</div>';return;}
  el.innerHTML=[...recs].reverse().slice(0,12).map(r=>`
    <div class="log-item">
      <div class="av" style="width:24px;height:24px;font-size:9px">${ini(r.empName)}</div>
      <div style="flex:1">
        <div style="font-weight:500;color:var(--navy)">${esc(r.empName)} <span class="site-badge">${esc(r.siteCode)}</span></div>
        <div style="font-size:10px;color:var(--gray)">${r.time.toLocaleTimeString('en-US',{hour:'2-digit',minute:'2-digit'})} · <span class="method-pill">${esc(r.method)}</span></div>
      </div>
      <span class="${r.type==='in'?'in-b':'out-b'}">${r.type.toUpperCase()}</span>
    </div>`).join('');
}

/* ── MY LOG ── */
function renderMyLog(){
  const el=document.getElementById('my-log-tbl');if(!el)return;
  // Show all records for current site (including Python camera records)
  const today=new Date();today.setHours(0,0,0,0);
  const siteRecs = sSite
    ? RECS.filter(r=>r.time>=today&&(r.siteId===sSite.id||r.source==='python'))
    : RECS.filter(r=>r.time>=today);
  const ms=document.getElementById('my-stats');
  if(ms)ms.innerHTML=`
    <div class="stat"><div class="stat-n">${siteRecs.length}</div><div class="stat-l">Total Records</div></div>
    <div class="stat"><div class="stat-n" style="color:var(--ok)">${siteRecs.filter(r=>r.type==='in').length}</div><div class="stat-l">Check-ins</div></div>
    <div class="stat"><div class="stat-n" style="color:var(--dg)">${siteRecs.filter(r=>r.type==='out').length}</div><div class="stat-l">Check-outs</div></div>
    <div class="stat"><div class="stat-n" style="color:#3b82f6">${siteRecs.filter(r=>r.source==='python').length}</div><div class="stat-l">Python Cam</div></div>`;
  // Show/hide python sync button
  const pyBtn=document.getElementById('py-sync-btn');
  if(pyBtn) pyBtn.style.display=pyServerOnline?'inline-flex':'none';
  if(!siteRecs.length){el.innerHTML=`<tr><td colspan="7"><div class="empty"><i class="ti ti-inbox"></i>No attendance records today</div></td></tr>`;return;}
  el.innerHTML=[...siteRecs].reverse().map(r=>{
    const isPy=r.source==='python';
    const methodBadge=isPy
      ? `<span style="background:#dbeafe;color:#1d4ed8;border-radius:4px;padding:2px 6px;font-size:9px;font-weight:600"><i class="ti ti-brand-python"></i> Python Cam</span>`
      : `<span class="method-pill">${esc(r.method)}</span>`;
    const conf=isPy&&r.confidence?`<span style="font-size:9px;color:#6b7280"> ${parseFloat(r.confidence).toFixed(0)}%</span>`:'';
    return `<tr${isPy?' style="background:#eff6ff"':''}>
    <td><div style="display:flex;align-items:center;gap:5px"><div class="av" style="width:22px;height:22px;font-size:8px">${ini(r.empName)}</div><div style="font-weight:500;color:var(--navy)">${esc(r.empName)}</div></div></td>
    <td><span class="site-badge">${esc(r.empId||'')}</span></td>
    <td><span class="${dCls(r.dept)}">${esc(r.dept||'')}</span></td>
    <td><span class="site-badge">${esc(r.siteCode||'Python')}</span></td>
    <td><span class="${r.type==='in'?'in-b':'out-b'}">${r.type.toUpperCase()}</span></td>
    <td>${methodBadge}${conf}</td>
    <td style="color:var(--dg);font-size:11px;font-family:var(--mono)">${r.time.toLocaleTimeString('en-US',{hour:'2-digit',minute:'2-digit'})}</td>
  </tr>`;}).join('');
}

/* ── CLOCK ── */
function tick(){
  const n=new Date();const ts=n.toLocaleTimeString('en-US',{hour:'2-digit',minute:'2-digit',second:'2-digit'});
  ['a-clk','s-clk'].forEach(id=>{const el=document.getElementById(id);if(el)el.textContent=ts;});
}
setInterval(tick,1000);tick();

/* ── LOCAL STORAGE PERSISTENCE ── */
function saveData(){
  try{
    localStorage.setItem('bdi_att_emps',JSON.stringify(EMPS));
    localStorage.setItem('bdi_att_sites',JSON.stringify(SITES));
    localStorage.setItem('bdi_att_sups',JSON.stringify(SUPS));
    localStorage.setItem('bdi_att_recs',JSON.stringify(RECS.map(r=>({...r,time:r.time.toISOString()}))));
    localStorage.setItem('bdi_att_ec',String(ec));
    localStorage.setItem('bdi_att_sc',String(sc));
  }catch(e){console.warn('Save failed',e);}
  // Push to central server so all devices get the update
  _pushToServer();
}

/* Push full app state to server */
async function _pushToServer(){
  if(!pyServerOnline) { _syncEmployeesToPython(); return; }
  try{
    const payload = {
      employees:   EMPS,
      sites:       SITES,
      supervisors: SUPS,
      records:     RECS.map(r=>({...r, time: r.time instanceof Date ? r.time.toISOString() : r.time})),
      settings:    SETTINGS,
      ec, sc,
      version:     _serverDataVersion
    };
    const r = await fetch(`${PY_SERVER}/data`, {
      method: 'POST',
      headers: {'Content-Type':'application/json'},
      body: JSON.stringify(payload)
    });
    const d = await r.json();
    if(d.ok) _serverDataVersion = d.version;
    console.log(`[SYNC] Data pushed to server (v${_serverDataVersion})`);
  }catch(e){ _syncEmployeesToPython(); }
}

/* Export employee list to Python server so face_enroll_python.py can read them */
async function _syncEmployeesToPython(){
  if(!pyServerOnline) return;
  try{
    const payload = EMPS.map(e=>({
      uid:  e.uid,
      name: e.name,
      id:   e.empId || e.uid,
      dept: e.dept  || '',
      site: (e.sites||[]).join(','),
      faceEnrolled: !!(e.faceReg || false)
    }));
    await fetch(`${PY_SERVER}/employees`, {
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body: JSON.stringify({employees: payload})
    });
  }catch(e){ /* server offline — ignore */ }
}
let _serverDataVersion = 0;

function loadData(){
  try{
    const em=localStorage.getItem('bdi_att_emps');if(em)EMPS=JSON.parse(em);
    const si=localStorage.getItem('bdi_att_sites');if(si)SITES=JSON.parse(si);
    const su=localStorage.getItem('bdi_att_sups');if(su)SUPS=JSON.parse(su);
    const re=localStorage.getItem('bdi_att_recs');if(re)RECS=JSON.parse(re).map(r=>({...r,time:new Date(r.time)}));
    const ec2=localStorage.getItem('bdi_att_ec');if(ec2)ec=parseInt(ec2);
    const sc2=localStorage.getItem('bdi_att_sc');if(sc2)sc=parseInt(sc2);
  }catch(e){console.warn('Load failed',e);}
  // Try to load from central server (overrides localStorage if server has data)
  _loadFromServer();
}

/* Load all data from central server — makes all devices share same data */
async function _loadFromServer(){
  if(!pyServerOnline) { _mergePyRecords(); return; }
  try{
    const r = await fetch(`${PY_SERVER}/data`);
    const d = await r.json();
    if(!d.ok) return;
    if(d.employees && d.employees.length) EMPS = d.employees;
    if(d.sites     && d.sites.length)     SITES = d.sites;
    if(d.supervisors && d.supervisors.length) SUPS = d.supervisors;
    if(d.records   && d.records.length){
      RECS = d.records.map(r=>({...r, time: new Date(r.time||r.timestamp)}));
    }
    if(d.ec) ec = d.ec;
    if(d.sc) sc = d.sc;
    if(d.settings) Object.assign(SETTINGS, d.settings);
    _serverDataVersion = d.version || 0;
    console.log(`[SYNC] Loaded from server — ${EMPS.length} employees, ${RECS.length} records (v${_serverDataVersion})`);
    // Re-render all views
    if(typeof renderAStats==='function') renderAStats();
    if(typeof renderEmpTbl==='function') renderEmpTbl();
    if(typeof renderReport==='function') renderReport();
    if(typeof renderSupDash==='function') renderSupDash();
    if(typeof renderMyLog ==='function') renderMyLog();
    if(typeof populateAllSelects==='function') populateAllSelects();
  }catch(e){ _mergePyRecords(); }
}

/* Poll server every 15s for changes from other devices */
setInterval(async ()=>{
  if(!pyServerOnline) return;
  try{
    const r = await fetch(`${PY_SERVER}/data/version`);
    const d = await r.json();
    if(d.version && d.version !== _serverDataVersion){
      console.log(`[SYNC] Server data changed (v${_serverDataVersion}→v${d.version}) — reloading`);
      _loadFromServer();
    }
  }catch(e){}
}, 15000);

/* Fetch attendance records punched via Python camera and merge into RECS */
async function _mergePyRecords(){
  if(!pyServerOnline) return;
  try{
    const r   = await fetch(`${PY_SERVER}/records`);
    const d   = await r.json();
    if(!d.ok || !d.records || !d.records.length) return;
    let added = 0;
    d.records.forEach(pr=>{
      // Avoid duplicates — match by uid + ISO timestamp
      const exists = RECS.some(x=>x.empUid===pr.empUid && x.time && new Date(x.time).toISOString()===pr.time);
      if(exists) return;
      // Find matching employee — enrich with site info if possible
      const emp  = EMPS.find(e=>e.uid===pr.empUid);
      const site = SITES.find(s=>s.id===pr.siteId) || SITES.find(s=>(emp?.sites||[]).includes(s.id));
      RECS.push({
        empUid:       pr.empUid,
        empName:      emp ? emp.name : pr.empName,
        empId:        emp ? emp.empId : pr.empId,
        dept:         emp ? emp.dept  : pr.dept,
        siteId:       site ? site.id   : (pr.siteId||''),
        siteName:     site ? site.name : (pr.siteName||'Python Camera'),
        siteCode:     site ? site.code : 'PYTHON-CAM',
        type:         pr.type,                    // 'in' or 'out'
        time:         new Date(pr.time),
        confidence:   pr.confidence,
        method:       'python-face',
        markedBy:     'python',
        markedByName: 'Python Camera',
        markedByRole: 'auto',
        timeStatus:   null,
        source:       'python'
      });
      added++;
    });
    if(added>0){
      console.log(`[PY] Merged ${added} Python attendance record(s) into reports`);
      // Re-render any open views
      if(typeof renderAStats==='function') renderAStats();
      if(typeof renderMyLog ==='function') renderMyLog();
      if(typeof renderEmpTbl==='function') renderEmpTbl();
    }
  }catch(e){ /* server offline */ }
}
function clearData(){
  if(!confirm('Reset ALL data (employees, sites, records)? This cannot be undone.'))return;
  ['bdi_att_emps','bdi_att_sites','bdi_att_sups','bdi_att_recs','bdi_att_ec','bdi_att_sc'].forEach(k=>localStorage.removeItem(k));
  EMPS=[];RECS=[];ec=100;sc=20;
  SITES=[{id:'S1',code:'ABD-HQ',name:'Abu Dhabi HQ Office',loc:'MBZ City, Abu Dhabi',lat:24.4539,lng:54.3773,radius:3},{id:'S2',code:'ABD-CW',name:'Corniche Pipeline Works',loc:'Corniche Road, Abu Dhabi',lat:24.4672,lng:54.3686,radius:2},{id:'S3',code:'SHJ-IND',name:'Sharjah Industrial Zone',loc:'Industrial Area, Sharjah',lat:25.3462,lng:55.4209,radius:3},{id:'S4',code:'AIN-01',name:'Al Ain Pipeline Project',loc:'Al Ain, Abu Dhabi',lat:24.2070,lng:55.7435,radius:4}];
  SUPS=[{uid:'SUP1',name:'Omar Al Rashidi',loginId:'sup001',role:'Site Supervisor',pw:'sup001',sites:['S1','S2']},{uid:'SUP2',name:'Mohammed Khalfan',loginId:'sup002',role:'Site Engineer',pw:'sup002',sites:['S3']},{uid:'SUP3',name:'Priya Nair',loginId:'sup003',role:'HSE Officer',pw:'sup003',sites:['S1','S3','S4']}];
  renderAStats();renderEmpTbl();renderSupTbl();renderSites();populateAllSelects();
  toast('All data reset to defaults',false);
}
/* Auto-save on data mutations */
const _origPushRec=pushRec;
pushRec=function(){const r=_origPushRec.apply(this,arguments);saveData();renderSupDash();return r;};
const _origAddEmp=addEmp;
addEmp=function(){_origAddEmp();saveData();};
const _origDelEmp=delEmp;
delEmp=function(u){_origDelEmp(u);saveData();};
const _origAddSup=addSup;
addSup=function(){_origAddSup();saveData();};
const _origDelSup=delSup;
delSup=function(u){_origDelSup(u);saveData();};
const _origAddSite=addSite;
addSite=function(){_origAddSite();saveData();};
const _origSaveSiteModal=saveSiteModal;
saveSiteModal=function(){_origSaveSiteModal();saveData();};
const _origMDeleteSite=mDeleteSite;
mDeleteSite=function(){_origMDeleteSite();saveData();};
const _origBMarkOne=bMarkOne;
bMarkOne=function(u){_origBMarkOne(u);saveData();};
const _origBMarkAll=bMarkAll;
bMarkAll=function(t){_origBMarkAll(t);saveData();};
const _origBClear=bClear;
bClear=function(){_origBClear();saveData();};
const _origLoadDemo=loadDemo;
loadDemo=function(){_origLoadDemo();saveData();};
/* Load on startup */
loadSettings();
loadData();
/* Add reset button to admin nav */
const _adminNav = document.querySelector('#scr-admin .subnav');
if (_adminNav) _adminNav.insertAdjacentHTML('beforeend','<button class="stab" onclick="clearData()" style="margin-left:auto;color:rgba(255,100,100,.6)"><i class="ti ti-trash"></i> Reset Data</button>');
function downloadReport(){
  if(!RECS.length){toast('No records to export',false);return;}
  const dateFrom=document.getElementById('r-date-from')?.value;
  const dateTo=document.getElementById('r-date-to')?.value;
  const fromTs=dateFrom?new Date(dateFrom+'T00:00:00').getTime():null;
  const toTs=dateTo?new Date(dateTo+'T23:59:59').getTime():null;
  let recs=RECS;
  if(fromTs)recs=recs.filter(r=>r.time.getTime()>=fromTs);
  if(toTs)recs=recs.filter(r=>r.time.getTime()<=toTs);
  if(!recs.length){toast('No records in selected date range',false);return;}
  let csv='Date,Employee,ID,Dept,Site,Type,Time,ArrivalStatus,MarkedBy,Method\n';
  recs.forEach(r=>{
    const ts=r.timeStatus?r.timeStatus.label:'';
    csv+=`${r.time.toLocaleDateString()},"${r.empName}",${r.empId},${r.dept},${r.siteCode},${r.type.toUpperCase()},${r.time.toLocaleTimeString()},"${ts}","${r.markedByName}",${r.method}\n`;
  });
  const blob=new Blob([csv],{type:'text/csv'});
  const url=URL.createObjectURL(blob);
  const a=document.createElement('a');
  a.href=url;a.download=`BDI_Attendance_${new Date().toISOString().slice(0,10)}.csv`;
  a.click();
  toast('Report exported to CSV');
}

function exportJSON() {
  const data = {
    version: '2.1', exported: new Date().toISOString(),
    settings: SETTINGS, employees: EMPS, sites: SITES,
    supervisors: SUPS, records: RECS.map(r=>({...r, time:r.time.toISOString()}))
  };
  const blob = new Blob([JSON.stringify(data, null, 2)], {type:'application/json'});
  const a = Object.assign(document.createElement('a'), {href:URL.createObjectURL(blob), download:`BDI_Backup_${new Date().toISOString().slice(0,10)}.json`});
  a.click(); toast('Full backup exported (JSON)');
}

function importJSON(input) {
  const file = input.files[0]; if(!file) return;
  const reader = new FileReader();
  reader.onload = e => {
    try {
      const d = JSON.parse(e.target.result);
      if(!d.employees || !d.sites) throw new Error('Invalid backup');
      if(!confirm(`Import backup from ${d.exported?.slice(0,10)||'unknown date'}?\nThis overwrites ALL current data.`)) return;
      EMPS = d.employees||[]; SITES = d.sites||[]; SUPS = d.supervisors||[];
      RECS = (d.records||[]).map(r=>({...r,time:new Date(r.time)}));
      ec = Math.max(ec, ...EMPS.map(e=>parseInt(e.uid?.replace('E',''))||0)) + 1;
      sc = Math.max(sc, ...SUPS.map(s=>parseInt(s.uid?.replace('SUP',''))||0)) + 1;
      if(d.settings) { SETTINGS={...SETTINGS,...d.settings}; saveSettings(); }
      saveData();
      initAdmin();
      toast('Backup imported successfully');
      input.value='';
    } catch(err) { toast('Import failed — invalid file', false); input.value=''; }
  };
  reader.readAsText(file);
}

/* API key banner removed as we use local face-api.js now */