// Sending a status change, with a short window to take it back.
//
// WHY THE DELAY EXISTS
// --------------------
// The action button gets pressed one-handed, outdoors, sometimes with gloves,
// on a phone held at arm's length. Mis-taps are not hypothetical. And the
// events table is append-only by design, so a mis-tap that reaches the server
// is permanent -- undoing it would need a compensating event, which is a
// feature nobody has asked for and which would put two conflicting records of
// the same moment in the log.
//
// So the correction happens BEFORE the write. Tapping starts a few seconds
// during which nothing has been sent and "Undo" simply forgets the whole
// thing. After the window it is on the server and gone for good, which is a
// boundary that can be explained in one sentence.
//
// WHY THIS IS A MODULE, NOT COMPONENT STATE
// -----------------------------------------
// A technician who taps "Arrived" and immediately hits back would, with a
// timer living in the component, lose the event on unmount -- strictly worse
// than having no undo at all. The pending write lives here so it outlives the
// screen, and navigating away FLUSHES it rather than dropping it.
//
// WHAT "SENT" MEANS NOW
// ---------------------
// It means WRITTEN TO INDEXEDDB, not accepted by the server. When the window
// closes the change is durable -- it survives a dead zone, a locked phone, a
// force-quit and a flat battery -- and lib/sync.ts gets it to the server
// whenever that becomes possible. The only failure this can still report is
// that the device would not store it at all, which is a different and much
// rarer kind of broken.

import type { JobStatus } from "@/lib/api";
import { enqueue } from "@/lib/outbox";
import { syncNow } from "@/lib/sync";
import { newUuid } from "@/lib/uuid";

/** Long enough to notice a mis-tap, short enough not to feel like lag. */
export const UNDO_WINDOW_MS = 5000;

export interface Pending {
  jobId: number;
  to: Exclude<JobStatus, "upcoming">;
  /** When the technician tapped -- NOT when it gets sent. */
  at: string;
}

/** Whether the change was safely stored. Not whether the server has it. */
type Settled = { stored: boolean };

interface Scheduled extends Pending {
  eventId: string;
  timer: ReturnType<typeof setTimeout>;
  onSettled: (outcome: Settled) => void;
}

// One at a time. A technician does one thing at a time, and allowing two
// pending writes would mean deciding what happens when they are for the same
// job -- a question with no good answer that phase 6's ordered queue answers
// properly.
let scheduled: Scheduled | null = null;

const listeners = new Set<(p: Pending | null) => void>();

function announce(): void {
  const snapshot = scheduled
    ? { jobId: scheduled.jobId, to: scheduled.to, at: scheduled.at }
    : null;
  for (const fn of listeners) fn(snapshot);
}

export function subscribe(fn: (p: Pending | null) => void): () => void {
  listeners.add(fn);
  fn(scheduled ? { jobId: scheduled.jobId, to: scheduled.to, at: scheduled.at } : null);
  return () => {
    listeners.delete(fn);
  };
}

export function pending(): Pending | null {
  return scheduled
    ? { jobId: scheduled.jobId, to: scheduled.to, at: scheduled.at }
    : null;
}

/**
 * Start the undo window for a status change.
 *
 * The event id and the timestamp are minted NOW, not at send time. That is
 * the point: `at` is when the technician said it happened, and the id is
 * fixed before anything leaves the device so a retry cannot become a second
 * event.
 */
export function schedule(
  jobId: number,
  to: Exclude<JobStatus, "upcoming">,
  onSettled: (outcome: Settled) => void,
): void {
  // A second tap while one is pending commits the first rather than losing
  // it. Reporting "arrived" then immediately "complete" is a real sequence.
  if (scheduled) flush();

  const entry: Scheduled = {
    jobId,
    to,
    at: new Date().toISOString(),
    eventId: newUuid(),
    onSettled,
    timer: setTimeout(() => {
      void send();
    }, UNDO_WINDOW_MS),
  };
  scheduled = entry;
  announce();
}

/** Forget the pending change. Nothing was sent, so nothing needs undoing. */
export function undo(): void {
  if (!scheduled) return;
  clearTimeout(scheduled.timer);
  scheduled = null;
  announce();
}

/** Send now instead of waiting out the window -- used when leaving a screen. */
export function flush(): void {
  if (!scheduled) return;
  clearTimeout(scheduled.timer);
  void send();
}

async function send(): Promise<void> {
  const entry = scheduled;
  if (!entry) return;
  // Cleared before the await, so an in-flight write cannot be undone or
  // double-flushed by anything that runs while it is happening.
  scheduled = null;
  announce();

  try {
    await enqueue({
      kind: "status",
      jobId: entry.jobId,
      payload: { id: entry.eventId, status: entry.to, at: entry.at },
    });
    entry.onSettled({ stored: true });
  } catch {
    // IndexedDB refused. Quota, a private window, or a device policy -- rare,
    // and genuinely unrecoverable here, so the UI reverts rather than
    // pretending the change is safe.
    entry.onSettled({ stored: false });
    return;
  }

  // Try immediately. If there is no signal this fails quietly and lib/sync.ts
  // takes over on the next `online` event, foreground, or backoff tick.
  void syncNow();
}
