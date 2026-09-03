import { hhmm } from "@/lib/format";
import type { SyncState } from "@/lib/sync";

/**
 * One thin line under the header, saying only what is currently true.
 *
 * Renders NOTHING when there is nothing to say -- fresh data, empty queue,
 * signal. That is the normal case and it should be silent. A permanent
 * "synced ✓" is clutter that trains people to stop reading the strip, which
 * is exactly the wrong habit for the one morning it says something important.
 *
 * The three things worth interrupting for, in the order they matter:
 *
 *   **How old is this?** Only when the last refresh did not succeed. A
 *   technician looking at a plan from 90 minutes ago needs to know before
 *   they drive somewhere.
 *
 *   **Is anything unsent?** The spec asks for a pending count, unobtrusively.
 *   It doubles as reassurance: taps in a basement visibly went somewhere.
 *
 *   **Did anything fail for good?** Rare, and the only line here a technician
 *   has to act on -- usually a job reassigned away while they were offline.
 *   Phase 8 turns this into a sentence naming the job.
 */
export function SyncStrip({
  sync,
  fetchedAt,
  stale,
  quietChanges = 0,
  onShowChanges,
}: {
  sync: SyncState;
  fetchedAt: string | null;
  stale: boolean;
  /** Changes worth mentioning but not worth taking the screen for. */
  quietChanges?: number;
  onShowChanges?: () => void;
}) {
  const parts: string[] = [];

  if (!sync.online) parts.push("Offline");
  if (stale && fetchedAt) parts.push(`updated ${hhmm(fetchedAt)}`);
  if (sync.pending > 0) {
    parts.push(
      sync.syncing
        ? `sending ${sync.pending}`
        : `${sync.pending} waiting to send`,
    );
  }

  if (parts.length === 0 && sync.failed === 0 && quietChanges === 0) return null;

  return (
    <div className="border-b border-line bg-paper px-4 pb-2.5">
      {parts.length > 0 ? (
        <p className="text-[0.9rem] font-semibold text-ink-soft">
          {/* Sentence case with middots, not badges. At arm's length in
              sunlight, one line of readable text beats three coloured pills. */}
          {parts.join(" · ")}
        </p>
      ) : null}

      {quietChanges > 0 && onShowChanges ? (
        // Retimes land here rather than taking over. Still one tap away, and
        // worded as the thing that changed rather than as a badge count.
        <button
          data-tappable
          type="button"
          onClick={onShowChanges}
          className="mt-1 min-h-[2.5rem] text-left text-[0.9rem] font-bold text-now underline underline-offset-4"
        >
          {quietChanges} time{quietChanges === 1 ? "" : "s"} changed — see what
          moved
        </button>
      ) : null}

      {sync.failed > 0 ? (
        <p className="mt-1 text-[0.9rem] font-bold text-alert">
          {sync.failed} update{sync.failed === 1 ? "" : "s"} couldn&rsquo;t be
          saved — check with dispatch
        </p>
      ) : null}
    </div>
  );
}
