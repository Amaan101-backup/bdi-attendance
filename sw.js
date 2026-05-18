/* BDI Attendance — Service Worker
   Caches app shell for offline use.
   Face recognition still needs server connection.
*/

const CACHE  = 'bdi-attendance-v3';
const ASSETS = [
  '/',
  '/admin',
  '/enroll',
  '/shared.js',
  '/shared.css',
  '/manifest.json'
];

// Install — cache core app shell
self.addEventListener('install', e => {
  e.waitUntil(
    caches.open(CACHE).then(c => c.addAll(ASSETS)).then(() => self.skipWaiting())
  );
});

// Activate — clean old caches
self.addEventListener('activate', e => {
  e.waitUntil(
    caches.keys().then(keys =>
      Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k)))
    ).then(() => self.clients.claim())
  );
});

// Fetch — serve from cache first, fall back to network
self.addEventListener('fetch', e => {
  const url = new URL(e.request.url);

  // Never cache API calls — always go to network
  if (url.pathname.startsWith('/status') ||
      url.pathname.startsWith('/punch')  ||
      url.pathname.startsWith('/recognize') ||
      url.pathname.startsWith('/verify') ||
      url.pathname.startsWith('/enroll') ||
      url.pathname.startsWith('/records') ||
      url.pathname.startsWith('/employees')) {
    e.respondWith(fetch(e.request).catch(() =>
      new Response(JSON.stringify({ok:false, error:'Offline — no server connection'}),
        {headers:{'Content-Type':'application/json'}})
    ));
    return;
  }

  // App shell — cache first
  e.respondWith(
    caches.match(e.request).then(cached => {
      if (cached) return cached;
      return fetch(e.request).then(res => {
        // Cache successful GET responses for app pages
        if (e.request.method === 'GET' && res.status === 200) {
          const clone = res.clone();
          caches.open(CACHE).then(c => c.put(e.request, clone));
        }
        return res;
      }).catch(() => {
        // Offline fallback page
        if (e.request.mode === 'navigate') {
          return caches.match('/');
        }
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
  // Get offline punches from IndexedDB and retry
  // (offline punches are queued in shared.js)
  const clients = await self.clients.matchAll();
  clients.forEach(c => c.postMessage({type: 'SYNC_COMPLETE'}));
}
