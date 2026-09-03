"use client";

import { useEffect } from "react";

/**
 * Registers the service worker, once, after hydration.
 *
 * Off in development by default: a caching service worker and Next's hot
 * reload fight each other, and the SW wins by serving yesterday's chunks
 * until you clear site data. Set NEXT_PUBLIC_ENABLE_SW=1 to register anyway
 * when you want to check installability without a production build.
 *
 * Renders nothing -- it exists purely for the effect. That is why it is a
 * separate client component rather than logic inside the layout: the layout
 * stays a server component and only this file ships to the browser.
 */
export function RegisterServiceWorker() {
  useEffect(() => {
    const enabled =
      process.env.NODE_ENV === "production" ||
      process.env.NEXT_PUBLIC_ENABLE_SW === "1";
    if (!enabled) return;
    if (!("serviceWorker" in navigator)) return;

    navigator.serviceWorker.register("/sw.js").catch((err) => {
      // A failed registration must never break the app -- it only costs the
      // offline shell. Phase 6 is where a failure here becomes worth
      // surfacing, because the queue depends on it.
      console.warn("service worker registration failed", err);
    });
  }, []);

  return null;
}
