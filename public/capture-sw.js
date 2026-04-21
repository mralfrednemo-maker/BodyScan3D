// BodyScan 3D Capture — Service Worker
// Minimal SW: just enough for "Add to Home Screen" installability.
// Network-first for all requests — no offline caching needed.

var CACHE_NAME = 'bs3d-capture-v1';
var SHELL_FILES = ['/capture.html', '/capture-manifest.json'];

self.addEventListener('install', function(e) {
  e.waitUntil(
    caches.open(CACHE_NAME).then(function(cache) {
      return cache.addAll(SHELL_FILES);
    })
  );
  self.skipWaiting();
});

self.addEventListener('activate', function(e) {
  e.waitUntil(
    caches.keys().then(function(keys) {
      return Promise.all(
        keys.filter(function(k) { return k !== CACHE_NAME; })
            .map(function(k) { return caches.delete(k); })
      );
    })
  );
  self.clients.claim();
});

// Network-first: always try network, fall back to cache only for shell files
self.addEventListener('fetch', function(e) {
  var url = new URL(e.request.url);
  // Only intercept same-origin requests
  if (url.origin !== self.location.origin) return;

  e.respondWith(
    fetch(e.request).catch(function() {
      return caches.match(e.request);
    })
  );
});
