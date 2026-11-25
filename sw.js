// Service worker de la app móvil: guarda la interfaz para abrirla sin conexión
// y la última copia de data.js para mostrar los últimos datos conocidos.
const CACHE = 'panel-mercado-v6';
const SHELL = ['./movil.html', './sim.js', './unlock.js', './manifest.webmanifest', './icon-192.png', './icon-512.png', './apple-touch-icon.png',
  'https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.min.js'];

self.addEventListener('install', e => {
  e.waitUntil(caches.open(CACHE).then(c => c.addAll(SHELL)).catch(() => {}).then(() => self.skipWaiting()));
});
self.addEventListener('activate', e => {
  e.waitUntil(caches.keys().then(ks => Promise.all(ks.filter(k => k !== CACHE).map(k => caches.delete(k))))
    .then(() => self.clients.claim()));
});
self.addEventListener('fetch', e => {
  const url = new URL(e.request.url);
  if (e.request.method !== 'GET') return;
  // datos, páginas y scripts propios: primero la red (siempre lo más nuevo), si falla la copia guardada
  const own = url.origin === location.origin;
  if (own && (/\.(js|html)$/.test(url.pathname) || url.pathname.endsWith('/'))) {
    const file = url.pathname.split('/').pop();
    const key = !file || file === 'index.html' ? './movil.html' : './' + file;
    e.respondWith(fetch(e.request).then(r => {
      if (r.ok) { const cp = r.clone(); caches.open(CACHE).then(c => c.put(key, cp)); }
      return r;
    }).catch(() => caches.match(key)));
    return;
  }
  e.respondWith(caches.match(e.request).then(r => r || fetch(e.request)));
});
