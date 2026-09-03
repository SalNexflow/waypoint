"use client";

import { useEffect, useState } from "react";
import type { FieldJob, JobStatus } from "@/lib/api";
import { NEXT } from "@/lib/status";
import {
  UNDO_WINDOW_MS,
  type Pending,
  flush,
  schedule,
  subscribe,
  undo,
} from "@/lib/status-writer";

/**
 * The one action: **On my way → Arrived → Complete**.
 *
 * Sticky to the bottom of the viewport, which on a phone is where the thumb
 * already is, and sized for a gloved one.
 *
 * Three states, and the middle one is the reason this component exists:
 *
 *  1. **Ready** -- the next transition, as a single large button.
 *  2. **Undo** -- for a few seconds after a tap, nothing has been sent and
 *     the whole thing can be forgotten. The status shown above has already
 *     changed, so the technician sees the result of their tap immediately and
 *     the correction is available without them having to think about it.
 *  3. **Stored** -- after the window the change is in IndexedDB and safe. It
 *     is NOT necessarily on the server, and that is fine: lib/sync.ts gets it
 *     there when there is signal. The only failure left to report is the
 *     device refusing to store it at all, which is rare and genuinely
 *     unrecoverable.
 */
export function ActionButton({
  job,
  optimistic,
  onOptimistic,
  onSettled,
  onComplete,
}: {
  job: FieldJob;
  /** Locally-applied status, ahead of the server. */
  optimistic: JobStatus | null;
  onOptimistic: (status: JobStatus | null) => void;
  onSettled: () => void;
  /** Opens the Complete form. The last step is not a one-tap transition. */
  onComplete: () => void;
}) {
  const [pending, setPending] = useState<Pending | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [seconds, setSeconds] = useState(0);

  useEffect(() => subscribe(setPending), []);

  // Leaving the screen COMMITS a pending change rather than dropping it. A
  // technician who taps "Arrived" and immediately hits back has reported
  // arriving; losing that would be worse than never offering undo.
  useEffect(() => () => flush(), []);

  const mine = pending?.jobId === job.id ? pending : null;

  // Countdown, purely so the window is legible. Without a number on it, a
  // button that vanishes after five seconds looks like a glitch.
  useEffect(() => {
    if (!mine) {
      setSeconds(0);
      return;
    }
    const deadline = Date.now() + UNDO_WINDOW_MS;
    const tick = () =>
      setSeconds(Math.max(0, Math.ceil((deadline - Date.now()) / 1000)));
    tick();
    const id = setInterval(tick, 250);
    return () => clearInterval(id);
  }, [mine]);

  const shown = optimistic ?? job.status;
  const next = NEXT[shown];

  function press() {
    if (!next) return;
    if (next.to === "complete") {
      // The last transition is not a tap, it is a form -- parts, notes, a
      // photo. No undo window either: the Complete screen IS the
      // confirmation step, and offering an undo on top of it would be a
      // second chance to change a mind that was just made up.
      onComplete();
      return;
    }
    setError(null);
    onOptimistic(next.to);
    schedule(job.id, next.to, (outcome) => {
      if (outcome.stored) {
        // Durable. The optimistic status is dropped here on purpose: from now
        // on the change is in the outbox, and useDay merges the outbox over
        // the cached day -- so it stays on screen across a reload, a screen
        // change and an app restart, which local component state would not.
        onOptimistic(null);
        onSettled();
      } else {
        onOptimistic(null);
        setError("This phone wouldn't save that. Check storage and try again.");
      }
    });
  }

  function cancel() {
    undo();
    onOptimistic(null);
  }

  return (
    <div className="fixed inset-x-0 bottom-0 border-t border-line bg-paper px-4 pt-3 pb-[calc(0.75rem+env(safe-area-inset-bottom))]">
      <div className="mx-auto w-full max-w-md">
        {error ? (
          <p
            role="alert"
            className="mb-2 rounded-lg bg-alert px-4 py-2.5 text-[0.95rem] font-semibold text-white"
          >
            {error}
          </p>
        ) : null}

        {mine ? (
          <button
            data-tappable
            type="button"
            onClick={cancel}
            // Bordered, not filled. Undo is a correction, not the thing the
            // technician came to do -- and it must not look like the next
            // step in the sequence they were just tapping through.
            className="min-h-[4.75rem] w-full rounded-xl border-2 border-now bg-paper text-[1.2rem] font-bold text-now"
          >
            Undo{seconds > 0 ? ` · ${seconds}` : ""}
          </button>
        ) : next ? (
          <button
            data-tappable
            type="button"
            onClick={press}
            className="min-h-[4.75rem] w-full rounded-xl bg-now text-[1.25rem] font-bold text-now-ink"
          >
            {next.label}
          </button>
        ) : (
          <p className="flex min-h-[4.75rem] items-center justify-center rounded-xl border-2 border-done text-[1.25rem] font-bold text-done">
            Done
          </p>
        )}
      </div>
    </div>
  );
}
