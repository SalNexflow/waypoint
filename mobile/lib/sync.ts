// Getting the outbox to the server, and telling the UI how that is going.
//
// Three separable things live here, in order of how much they matter:
//
//   1. classify()  -- is this failure worth retrying, or never?
//   2. drain()     -- send pending items in order, stopping at the right time
//   3. the syncer  -- when to run a drain, and how often to keep trying
//
// The first two take their dependencies as arguments so they can be tested
// directly. Only the third knows about timers, events and the network.

import { ApiError, postAck, postCompletion, postStatus } from "@/lib/api";
import {
  type OutboxItem,
  counts,
  markFailed,
  noteAttempt,
  pendingItems,
  remove,
} from "@/lib/outbox";
import { getToken } from "@/lib/session";

export type Outcome = "sent" | "retry" | "permanent" | "auth";

/**
 * What to do about a failed send.
 *
 * The distinction that matters is **can this ever succeed**. Retrying forever
 * is correct for a dead zone and wrong for a job that was reassigned to
 * someone else an hour ago: the first is the whole point of the queue, the
 * second wedges it permanently behind an item that will 404 until the end of
 * time.
 *
 *   `retry`      -- no connection, server down, rate-limited, timed out.
 *   `permanent`  -- the request itself is wrong or no longer allowed. A 404
 *                   from `/field/jobs/{id}/status` means the job is not this
 *                   technician's any more, which is exactly what a
 *                   reassignment looks like from the phone.
 *   `auth`       -- the token is dead. NOT the item's fault, so the item is
 *                   left untouched and the drain stops; it will go out after
 *                   the technician signs in again.
 *
 * 408 and 429 are 4xx that mean "later", not "never", so they are called out
 * rather than swept up with the rest.
 */
export function classify(err: unknown): Exclude<Outcome, "sent"> {
  if (!(err instanceof ApiError)) return "retry"; // network-level: no reply at all
  if (err.status === 401) return "auth";
  if (err.status === 408 || err.status === 429) return "retry";
  if (err.status >= 400 && err.status < 500) return "permanent";
  return "retry";
}

export interface DrainResult {
  sent: number;
  failed: number;
  /** Why the drain stopped early, if it did. */
  stopped: "offline" | "auth" | null;
  remaining: number;
}

/**
 * Send pending items, oldest first.
 *
 * **Stops at the first retryable failure.** Not "skips it and carries on":
 * the queue is ordered, and phase 7's completion has to land after the
 * `complete` status event it belongs to. Pressing on past a failure would
 * deliver them out of order the moment one item's send failed and the next
 * one's succeeded.
 *
 * **A permanent failure does not stop it.** That item is marked and stepped
 * over, because one dead item must not hold every later one hostage.
 *
 * Items are deleted only after a response. `duplicate: true` counts as
 * success -- it means a previous attempt got through and the reply was lost,
 * which is precisely the case the client-generated id exists to make safe.
 *
 * `send` is injected so this can be tested against every failure mode without
 * a network.
 */
export async function drain(
  send: (item: OutboxItem) => Promise<void>,
): Promise<DrainResult> {
  const items = await pendingItems();
  let sent = 0;
  let failed = 0;

  for (const item of items) {
    try {
      await send(item);
      await remove(item.seq);
      sent += 1;
    } catch (err) {
      const outcome = classify(err);
      const message = err instanceof Error ? err.message : String(err);

      if (outcome === "auth") {
        await noteAttempt(item.seq, message);
        return { sent, failed, stopped: "auth", remaining: (await counts()).pending };
      }
      if (outcome === "retry") {
        await noteAttempt(item.seq, message);
        return {
          sent,
          failed,
          stopped: "offline",
          remaining: (await counts()).pending,
        };
      }
      await markFailed(
        item.seq,
        err instanceof ApiError ? err.status : 0,
        message,
      );
      failed += 1;
    }
  }

  return { sent, failed, stopped: null, remaining: (await counts()).pending };
}

// --- The syncer -------------------------------------------------------------

export interface SyncState {
  pending: number;
  failed: number;
  syncing: boolean;
  online: boolean;
  /** When the outbox last actually delivered something. */
  lastSyncAt: string | null;
}

let state: SyncState = {
  pending: 0,
  failed: 0,
  syncing: false,
  online: true,
  lastSyncAt: null,
};

const listeners = new Set<(s: SyncState) => void>();

function publish(patch: Partial<SyncState>): void {
  state = { ...state, ...patch };
  for (const fn of listeners) fn(state);
}

export function syncState(): SyncState {
  return state;
}

export function subscribeSync(fn: (s: SyncState) => void): () => void {
  listeners.add(fn);
  fn(state);
  return () => {
    listeners.delete(fn);
  };
}

/** Recount from the database. Cheap, and the only source of these numbers. */
export async function refreshCounts(): Promise<void> {
  const c = await counts();
  publish({ pending: c.pending, failed: c.failed });
}

async function sendItem(item: OutboxItem, token: string): Promise<void> {
  switch (item.kind) {
    case "status":
      await postStatus(token, item.jobId, {
        id: item.payload.id,
        status: item.payload.status,
        at: item.payload.at,
        // The queue position IS the device sequence. One transactional
        // counter for both, so a gap can only mean an item was sent, never
        // that two items shared an order.
        device_seq: item.seq,
      });
      return;
    case "completion":
      await postCompletion(token, item.jobId, item.payload);
      return;
    case "ack":
      await postAck(token, item.payload.changeId);
      return;
  }
}

// --- Running it -------------------------------------------------------------

// Backoff between failed drains: start soon enough to catch a brief dead zone,
// back off far enough not to sit in a retry loop burning battery in a
// basement. Reset the moment anything succeeds or the radio comes back.
const BACKOFF_MS = [5_000, 10_000, 20_000, 40_000, 60_000];
let backoffStep = 0;
let timer: ReturnType<typeof setTimeout> | null = null;

// One drain at a time, coalescing everything that asks while it runs. Two
// concurrent drains would both read the same pending item and both send it --
// which the server would deduplicate on the event id, but which would still be
// the app doing the wrong thing and relying on the server to cover for it.
let running: Promise<DrainResult> | null = null;
let rerun = false;

export function syncNow(): Promise<DrainResult> {
  if (running) {
    rerun = true;
    return running;
  }
  running = (async () => {
    publish({ syncing: true });
    try {
      const token = getToken();
      if (!token) {
        return { sent: 0, failed: 0, stopped: "auth" as const, remaining: 0 };
      }

      const result = await drain((item) => sendItem(item, token));

      if (result.stopped === null) {
        backoffStep = 0;
        // Only when something actually went out. This timestamp is what tells
        // useDay() to refetch the day, and a drain that found an empty queue
        // has changed nothing on the server -- publishing it then would make
        // every app open cost two round trips instead of one.
        if (result.sent > 0) publish({ lastSyncAt: new Date().toISOString() });
      } else if (result.stopped === "offline") {
        backoffStep = Math.min(backoffStep + 1, BACKOFF_MS.length - 1);
        scheduleRetry();
      }
      return result;
    } finally {
      publish({ syncing: false });
      await refreshCounts();
      running = null;
      if (rerun) {
        rerun = false;
        void syncNow();
      }
    }
  })();
  return running;
}

function scheduleRetry(): void {
  if (timer) clearTimeout(timer);
  timer = setTimeout(() => {
    timer = null;
    void syncNow();
  }, BACKOFF_MS[backoffStep]);
}

let started = false;

/**
 * Start listening for the moments worth trying again.
 *
 * Deliberately NOT the Background Sync API. `registration.sync` would let the
 * service worker drain with the app closed, and it is Chromium-only -- no
 * Safari, no Firefox -- and would need a second copy of the queue logic and
 * the bearer token inside the worker. For an app installed to a home screen
 * and left open across a shift, the moments that actually matter are the
 * radio coming back and the technician looking at the screen, both of which
 * the page sees. Worth revisiting at phase 10 if closed-app sync turns out to
 * matter on a real device; it is a real divergence from the spec's wording.
 */
export function startSyncing(): void {
  if (started || typeof window === "undefined") return;
  started = true;

  publish({ online: navigator.onLine });

  window.addEventListener("online", () => {
    publish({ online: true });
    backoffStep = 0; // the radio is back; do not sit out the old backoff
    void syncNow();
  });
  window.addEventListener("offline", () => publish({ online: false }));

  // Coming back to the app is the other reliable signal. A phone that was in
  // a pocket may have regained signal without firing `online`, because it
  // never lost the event loop -- it was simply never asked.
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible") void syncNow();
  });

  void refreshCounts().then(() => syncNow());
}

/** Test seam. */
export function resetSyncForTests(): void {
  if (timer) clearTimeout(timer);
  timer = null;
  backoffStep = 0;
  running = null;
  rerun = false;
  started = false;
  state = {
    pending: 0,
    failed: 0,
    syncing: false,
    online: true,
    lastSyncAt: null,
  };
  listeners.clear();
}
