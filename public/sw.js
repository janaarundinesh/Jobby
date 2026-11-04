/* Jobby — service worker.
   Makes the dashboard installable and lets it open from the app icon even when
   the Python server is not running yet. The API is never cached: job data must
   always come from the server. */

const CACHE = "jobpipe-shell-v1";

const SHELL = [
  "/",
  "/index.html",
  "/styles.css",
  "/img/car.jpg",
  "/manifest.webmanifest",
  "/favicon.svg",
  "/icon-192.png",
  "/icon-512.png",
  "/apple-touch-icon.png",
  "/js/main.js",
  "/js/core.js",
  "/js/data.js",
  "/js/overview.js",
  "/js/workspace.js",
  "/js/drawer.js",
  "/js/dialogs.js",
  "/js/profiles.js",
  "/js/io.js",
  "/js/a11y.js",
  "/js/reminders.js",
  "/js/resume.js",
  "/js/pwa.js",
  "/js/status.js",
];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches
      .open(CACHE)
      // One missing file must not fail the whole install.
      .then((cache) => Promise.all(SHELL.map((path) => cache.add(path).catch(() => null))))
      .then(() => self.skipWaiting())
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) => Promise.all(keys.filter((key) => key !== CACHE).map((key) => caches.delete(key))))
      .then(() => self.clients.claim())
  );
});

/* Network first: the shell is only a fallback for when the server is down, so a
   reload always shows the code that is actually on disk. */
async function networkFirst(request) {
  const cache = await caches.open(CACHE);
  try {
    const response = await fetch(request);
    if (response && response.ok && response.type === "basic") cache.put(request, response.clone());
    return response;
  } catch (error) {
    const cached = await cache.match(request, { ignoreSearch: true });
    if (cached) return cached;
    if (request.mode === "navigate") {
      const shell = await cache.match("/index.html");
      if (shell) return shell;
    }
    throw error;
  }
}

self.addEventListener("fetch", (event) => {
  const request = event.request;
  if (request.method !== "GET") return;
  const url = new URL(request.url);
  if (url.origin !== self.location.origin) return;
  // Job data, settings and documents always come from the server.
  if (url.pathname.startsWith("/api/")) return;
  event.respondWith(networkFirst(request));
});

// The daily reminder is posted through the worker so it works where pages are
// not allowed to raise notifications themselves.
self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  event.waitUntil(
    self.clients.matchAll({ type: "window", includeUncontrolled: true }).then((list) => {
      const open = list.find((client) => new URL(client.url).origin === self.location.origin);
      if (open) return open.focus();
      return self.clients.openWindow("/");
    })
  );
});
