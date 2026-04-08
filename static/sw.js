self.addEventListener('install', (event) => {
  self.skipWaiting();
});

self.addEventListener('activate', (event) => {
  return self.clients.claim();
});

// A simple fetch handler that allows offline caching can go here.
// For TWA, mostly an empty or basic service worker is enough if the site is constantly online,
// but a basic offline fallback is recommended by Play Store requirements.
self.addEventListener('fetch', function(event) {
  event.respondWith(
    fetch(event.request).catch(function() {
      return caches.match(event.request).then(function(response) {
        return response || new Response("Offline Mode", {
          status: 503,
          statusText: "Service Unavailable",
          headers: new Headers({ "Content-Type": "text/plain" })
        });
      });
    })
  );
});
