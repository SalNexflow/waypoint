/*
 * Waypoint Field service worker.
 *
 * Phase 1 scope: make the app shell installable and openable with no signal.
 * It caches the shell and static assets and nothing else. It deliberately
 * does NOT touch the API -- every cross-origin request passes straight
 * through. Caching `GET /field/today` and queueing writes is phase 6, and
 * doing half of it here would leave two places that own offline behaviour.
 *
 * Hand-written rather than Workbox / next-pwa. Workbox is the conventional
 * choice and would be defensible; next-pwa is effectively unmaintained. This
 * file is ~90 lines, adds no dependency, and phase 6 needs exact control over
 * queue draining and replay ordering -- which is precisely the part of
 * Workbox you end up fighting.
 */

// Bump on every change to this file or to the precache list. The old cache is
// deleted on activate, so a stale shell cannot outlive a deploy.
const VERSION = "waypoint-field-v3";

// The minimum needed to render something useful with the radio off.
//
// `/job` is here without an id on purpose. It is one static document that
// serves every job -- the id arrives as a query parameter and the data comes
// from the cached day -- so caching it once covers deep-linking to any job
// with the radio off. A `/job/[id]` path segment could not have been
// precached at all without knowing every id in advance.
const SHELL = [
  "/",
  "/job",
  "/complete",
  "/manifest.webmanifest",
  "/icons/icon-192.png",
];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(VERSION).then((cache) => cache.addAll(SHELL)),
  );
  // No skipWaiting, deliberately. Next serves content-hashed chunks; swapping
  // the worker under a page that is already running means a client-side
  // navigation can ask for a chunk that no longer exists, which surfaces to
  // the technician as a blank screen. The new worker takes over on the next
  // cold start instead. Phase 10 should add an explicit "update available"
  // prompt -- an app someone keeps open for a nine-hour shift otherwise never
  // updates.
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    (async () => {
      const names = await caches.keys();
      await Promise.all(
        names
          .filter((n) => n.startsWith("waypoint-field-") && n !== VERSION)
          .map((n) => caches.delete(n)),
      );
      // Take control of pages loaded before this worker existed, so the very
      // first visit is offline-capable without a second reload.
      await self.clients.claim();
    })(),
  );
});

self.addEventListener("fetch", (event) => {
  const req = event.request;

  // Writes are never cached, and never replayed from here. Phase 6 owns that.
  if (req.method !== "GET") return;

  const url = new URL(req.url);

  // The API is a different origin (:8000). Leave it entirely alone: an
  // opaque cached response would be worse than no response, because the UI
  // could not tell the difference between fresh and hours-stale data.
  if (url.origin !== self.location.origin) return;

  // Content-hashed and immutable. Cache-first is safe by construction: a
  // changed file has a different URL.
  if (url.pathname.startsWith("/_next/static/")) {
    event.respondWith(cacheFirst(req));
    return;
  }

  // Page loads. Network-first so a technician with signal always sees the
  // current shell, cache-fallback so one without still gets in.
  if (req.mode === "navigate") {
    event.respondWith(navigationHandler(req));
    return;
  }

  // Icons, manifest, fonts. Serve immediately, refresh in the background.
  event.respondWith(staleWhileRevalidate(req));
});

async function cacheFirst(req) {
  const hit = await caches.match(req);
  if (hit) return hit;
  const res = await fetch(req);
  if (res.ok) (await caches.open(VERSION)).put(req, res.clone());
  return res;
}

async function navigationHandler(req) {
  try {
    const res = await fetch(req);
    if (res.ok) (await caches.open(VERSION)).put(req, res.clone());
    return res;
  } catch {
    // Offline. `ignoreSearch` matters: the cached document is `/job`, and
    // without it a request for `/job?id=4412` misses and falls through to
    // Today -- the technician taps a job and lands back where they started.
    return (
      (await caches.match(req, { ignoreSearch: true })) ||
      (await caches.match("/")) ||
      Response.error()
    );
  }
}

async function staleWhileRevalidate(req) {
  const cache = await caches.open(VERSION);
  const hit = await cache.match(req);
  const network = fetch(req)
    .then((res) => {
      if (res.ok) cache.put(req, res.clone());
      return res;
    })
    .catch(() => undefined);
  return hit || (await network) || Response.error();
}
