/* Doctor Money service worker: always tries the network first so a new deploy shows up at once,
   and falls back to the last saved copy when the device is offline. API calls go to another origin and are not touched. */
var CACHE = 'doctor-money-v1';
var SHELL = ['/dashboard', '/dr-robot.png', '/icons/icon-192.png'];

self.addEventListener('install', function (event) {
  event.waitUntil(caches.open(CACHE).then(function (cache) { return cache.addAll(SHELL); }).catch(function () {}));
  self.skipWaiting();
});

self.addEventListener('activate', function (event) {
  event.waitUntil(caches.keys().then(function (keys) {
    return Promise.all(keys.filter(function (key) { return key !== CACHE; }).map(function (key) { return caches.delete(key); }));
  }).then(function () { return self.clients.claim(); }));
});

self.addEventListener('fetch', function (event) {
  var request = event.request, url = new URL(request.url);
  if (request.method !== 'GET' || url.origin !== self.location.origin || url.pathname === '/admin') return;
  // Query strings such as ?plan= or ?ai=1 only steer the page script, so every /dashboard request shares one saved copy.
  var key = request.mode === 'navigate' ? url.pathname : request;
  event.respondWith(fetch(request).then(function (response) {
    if (response.ok) { var copy = response.clone(); caches.open(CACHE).then(function (cache) { cache.put(key, copy); }); }
    return response;
  }).catch(function () {
    return caches.match(key).then(function (saved) { return saved || (request.mode === 'navigate' ? caches.match('/dashboard') : Response.error()); });
  }));
});
