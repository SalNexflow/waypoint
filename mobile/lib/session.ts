// The technician's session: the bearer token, and just enough about them to
// render the header before the network answers.
//
// localStorage, not IndexedDB, even though phase 6 brings IndexedDB in for the
// write queue. Two reasons, and the first is the one that matters:
//
//   1. localStorage is SYNCHRONOUS. The token can be read inside a render
//      rather than in an effect, so there is no frame where the app knows
//      nothing and has to show a spinner. The whole app is built around not
//      putting a spinner on the critical path, and an async credential read
//      would put one there before anything else could happen.
//
//   2. Its failure mode is the right one. A queue of unsynced status updates
//      must survive anything; a token that is lost just means signing in
//      again, which is a thirty-second inconvenience, not lost work.
//
// It survives app close and device restart, which is the requirement.

import type { TechnicianMe } from "@/lib/api";
import { clearDay } from "@/lib/day-cache";

const TOKEN_KEY = "waypoint.field.token";
const TECH_KEY = "waypoint.field.technician";

/**
 * `typeof window === "undefined"` is the check for "this is running on the
 * server". Next prerenders these pages at build time in Node, where there is
 * no localStorage, and touching it there throws. Every accessor here returns
 * a safe empty value in that case, so the prerendered HTML is the signed-out
 * shell and the real state appears on mount.
 */
function available(): boolean {
  return typeof window !== "undefined" && !!window.localStorage;
}

export function getToken(): string | null {
  if (!available()) return null;
  try {
    return window.localStorage.getItem(TOKEN_KEY);
  } catch {
    // Private browsing and some locked-down device policies make
    // localStorage throw on access rather than return null.
    return null;
  }
}

export function getCachedTechnician(): TechnicianMe | null {
  if (!available()) return null;
  try {
    const raw = window.localStorage.getItem(TECH_KEY);
    return raw ? (JSON.parse(raw) as TechnicianMe) : null;
  } catch {
    return null;
  }
}

export function saveSession(token: string, technician: TechnicianMe): void {
  if (!available()) return;
  try {
    window.localStorage.setItem(TOKEN_KEY, token);
    window.localStorage.setItem(TECH_KEY, JSON.stringify(technician));
  } catch {
    /* nothing useful to do; the app still works for this session */
  }
}

export function cacheTechnician(technician: TechnicianMe): void {
  if (!available()) return;
  try {
    window.localStorage.setItem(TECH_KEY, JSON.stringify(technician));
  } catch {
    /* ignore */
  }
}

export function clearSession(): void {
  if (!available()) return;
  try {
    window.localStorage.removeItem(TOKEN_KEY);
    window.localStorage.removeItem(TECH_KEY);
    // The cached day goes too. It is one technician's work, and a shared
    // handset is not a hypothetical in this trade -- without this, the next
    // person to sign in would see the previous one's jobs painted on the
    // first frame, before any request had been made.
    //
    // The outbox is deliberately NOT cleared: it may hold work that has not
    // reached the server, which is why signing out is refused while anything
    // is unsent rather than being allowed to discard it here.
    clearDay();
  } catch {
    /* ignore */
  }
}
