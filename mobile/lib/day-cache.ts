// The last known day, kept so the app opens with something on screen.
//
// WHY localStorage AND NOT IndexedDB
// ----------------------------------
// The queue lives in IndexedDB because it holds work that cannot be
// regenerated. This is the opposite: a cache of something the server will
// happily send again. What it needs is not durability, it is SPEED --
// specifically, being readable during the first render.
//
// localStorage is synchronous, so the cached day can be read inside a render
// and painted with no await and no frame of nothing. IndexedDB cannot: every
// read is a promise, so the first paint would be a skeleton no matter how
// fast the disk is. "It opens, it shows the next job. No spinner on the
// critical path" is a constraint, and one await is enough to break it.
//
// The trade is that localStorage can be evicted under storage pressure, and
// for a cache that is the right thing to lose.

import type { FieldDay } from "@/lib/api";

const KEY = "waypoint.field.day";

interface Cached {
  day: FieldDay;
  /** When this was received. Drives the "updated HH:MM" marker. */
  fetchedAt: string;
}

export function saveDay(day: FieldDay): void {
  try {
    const entry: Cached = { day, fetchedAt: new Date().toISOString() };
    window.localStorage.setItem(KEY, JSON.stringify(entry));
  } catch {
    // Quota, private browsing, or a device policy. The app still works with
    // signal; it just cannot open offline. Not worth interrupting anyone over.
  }
}

/**
 * The cached day, if there is one and it belongs to this technician, today.
 *
 * Two guards, and both are about showing somebody the wrong day.
 *
 * **The date.** A cache from yesterday is treated as no cache at all. Showing
 * a technician yesterday's jobs at 7am with a stale "updated 16:40" marker is
 * worse than showing nothing, because it looks exactly like a real day and
 * they would drive to the first address on it.
 *
 * **The technician.** Two people share a van and a spare handset more often
 * than anyone plans for. `clearSession()` already wipes this cache on sign
 * out, so this check should never fire -- which is exactly why it is here.
 * Everything else in the system enforces scoping structurally (the token
 * decides whose day it is, and there is no technician_id parameter under
 * /field to get wrong); a cache keyed only by date would be the one place a
 * technician could see another's work, and it would happen on the first frame
 * before any request was made.
 */
export function loadDay(
  expectedDay: string,
  expectedTechnicianId: number | null,
): Cached | null {
  if (typeof window === "undefined") return null;
  try {
    const raw = window.localStorage.getItem(KEY);
    if (!raw) return null;
    const entry = JSON.parse(raw) as Cached;
    if (!entry?.day?.jobs || entry.day.day !== expectedDay) return null;
    if (
      expectedTechnicianId !== null &&
      entry.day.technician_id !== expectedTechnicianId
    ) {
      return null;
    }
    return entry;
  } catch {
    return null;
  }
}

export function clearDay(): void {
  try {
    window.localStorage.removeItem(KEY);
  } catch {
    /* nothing useful to do */
  }
}

/**
 * Today's date in the dispatch timezone, as YYYY-MM-DD.
 *
 * Computed from the device clock, which is the one place the app has no
 * choice -- but pinned to Malaysia rather than the device's own timezone, so
 * a handset left on UTC still asks for the right day. A phone whose DATE is
 * wrong will ask for the wrong day and get an empty one, which is visible and
 * recoverable, rather than silently rendering the wrong day's jobs.
 */
export function localDay(now: Date = new Date()): string {
  // en-CA formats as YYYY-MM-DD, which is the shape the API wants, without
  // hand-rolling a formatter or pulling in a date library.
  return new Intl.DateTimeFormat("en-CA", {
    timeZone: "Asia/Kuala_Lumpur",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).format(now);
}
