/* BDI Attendance — Service Worker
   HTML pages → network-first (always fresh from server)
   Static assets → cache-first (fonts, css, js)
   API calls → network only (never cached)
*/

const CACHE  = 'bdi-attendance-v6';
const STATIC = [
  '/shared.js',
  '/shared.css',
  '/manifest.json'
];

// Install — pre-cache only static assets (NOT html pages)
self.addEventListener('install', e => {
  e.waitUntil(
    caches.open(CACHE).then(c => c.addAll(STATIC)).then(() => self.skipWaiting())
  );
});

// Activate — delete ALL old caches
self.addEventListener('activate', e => {
  e.waitUntil(
    caches.keys().then(keys =>
      Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k)))
    ).then(() => self.clients.claim())
  );
});

// Fetch strategy
self.addEventListener('fetch', e => {
  const url = new URL(e.request.url);

  // 1. API calls — NEVER cache, always network
  if (url.pathname.startsWith('/status') ||
      url.pathname.startsWith('/punch')  ||
      url.pathname.startsWith('/recognize') ||
      url.pathname.startsWith('/verify') ||
      url.pathname.startsWith('/enroll') ||
      url.pathname.startsWith('/records') ||
      url.pathname.startsWith('/employees') ||
      url.pathname.startsWith('/data') ||
      url.pathname.startsWith('/app/')) {
    e.respondWith(fetch(e.request).catch(() =>
      new Response(JSON.stringify({ok:false, error:'Offline — no server connection'}),
        {headers:{'Content-Type':'application/json'}})
    ));
    return;
  }

  // 2. HTML navigation (/, /admin, /enroll, etc.) — NETWORK FIRST
  //    Always get fresh HTML from server; only use cache if offline
  if (e.request.mode === 'navigate' || url.pathname.endsWith('.html') ||
      url.pathname === '/' || url.pathname === '/admin' || url.pathname === '/enroll') {
    e.respondWith(
      fetch(e.request).then(res => {
        // Got fresh response — update cache and return it
        if (res.status === 200) {
          const clone = res.clone();
          caches.open(CACHE).then(c => c.put(e.request, clone));
        }
        return res;
      }).catch(() => {
        // Offline — serve cached version if available
        return caches.match(e.request).then(cached => cached ||
          new Response('<h1>Offline</h1><p>No internet connection.</p>',
            {headers:{'Content-Type':'text/html'}})
        );
      })
    );
    return;
  }

  // 3. Static assets (JS, CSS, fonts) — cache first, network fallback
  e.respondWith(
    caches.match(e.request).then(cached => {
      if (cached) return cached;
      return fetch(e.request).then(res => {
        if (e.request.method === 'GET' && res.status === 200) {
          const clone = res.clone();
          caches.open(CACHE).then(c => c.put(e.request, clone));
        }
        return res;
      });
    })
  );
});

// Background sync — retry failed punches when back online
self.addEventListener('sync', e => {
  if (e.tag === 'sync-punches') {
    e.waitUntil(syncOfflinePunches());
  }
});

async function syncOfflinePunches() {
  const clients = await self.clients.matchAll();
  clients.forEach(c => c.postMessage({type: 'SYNC_COMPLETE'}));
}
