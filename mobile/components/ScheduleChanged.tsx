"use client";

import { type ChangeKind, type ScheduleChange } from "@/lib/api";
import { hhmm } from "@/lib/format";
import type { OutboxItem } from "@/lib/outbox";

/**
 * Schedule changed -- screen 4 of 4.
 *
 * The only screen nobody navigates to. It takes over, because the thing it
 * says is the thing this app exists to replace: the phone call that starts
 * "don't go to Ampang, Siti's taking it".
 *
 * A takeover has to be rare to work. It fires only for the kinds the spec
 * names -- reassigned, cancelled, newly given -- and never for a retime on
 * its own. A re-solve moves several jobs by a quarter of an hour most times
 * it runs, and interrupting on each would teach people to dismiss without
 * reading, which costs exactly the one that mattered.
 *
 * One button for all of it, not one per card. "Got it" is an acknowledgement
 * that they have taken the situation in, and making somebody tap four times
 * to say that once is how a notice becomes an obstacle.
 */

const HEADLINE: Record<ChangeKind, string> = {
  removed: "Taken off you",
  cancelled: "Cancelled",
  assigned: "Added to your day",
  retimed: "Moved",
};

export function ScheduleChanged({
  changes,
  failures,
  onAcknowledge,
}: {
  changes: ScheduleChange[];
  /** Locally-failed writes, usually the same reassignment from this side. */
  failures: OutboxItem[];
  onAcknowledge: () => void;
}) {
  const disruptive = changes.filter((c) => c.kind !== "retimed");
  const retimes = changes.filter((c) => c.kind === "retimed");

  return (
    <main
      // Fixed and opaque: this is not a banner over the day, it is instead of
      // the day. Anything visible behind it would invite reading around it.
      className="fixed inset-0 z-50 flex flex-col overflow-y-auto bg-ground"
      role="alertdialog"
      aria-modal="true"
      aria-labelledby="changed-title"
    >
      <div className="mx-auto flex w-full max-w-md flex-1 flex-col px-5 pt-10 pb-40">
        <h1 id="changed-title" className="text-[2rem] leading-tight font-bold">
          Your day changed
        </h1>
        <p className="mt-2 text-[1.05rem] text-ink-soft">
          {disruptive.length > 0 || failures.length > 0
            ? "Check this before you drive."
            : "Some of your times moved."}
        </p>

        <div className="mt-6 flex flex-col gap-3">
          {/* The disruptive ones first, always. A re-solve that moves one job
              to somebody else also nudges half a dozen others by twenty
              minutes, and in arrival order the sentence that changes where the
              technician drives lands fifth on the screen -- below the fold,
              under four things that do not matter nearly as much. */}
          {failures.map((item) => (
            <FailureCard key={`f${item.seq}`} item={item} />
          ))}

          {disruptive.map((change) => (
            <ChangeCard key={change.id} change={change} />
          ))}

          {/* And the rest as one card. Seven full-size cards is a wall, and a
              wall gets dismissed rather than read. */}
          {retimes.length > 0 ? <RetimeCard changes={retimes} /> : null}
        </div>
      </div>

      {/* Sticky, thumb-height, and the only way out. */}
      <div className="fixed inset-x-0 bottom-0 border-t border-line bg-paper px-4 pt-3 pb-[calc(0.75rem+env(safe-area-inset-bottom))]">
        <div className="mx-auto w-full max-w-md">
          <button
            data-tappable
            type="button"
            onClick={onAcknowledge}
            className="min-h-[4.75rem] w-full rounded-xl bg-now text-[1.25rem] font-bold text-now-ink"
          >
            Got it
          </button>
        </div>
      </div>
    </main>
  );
}

function ChangeCard({ change }: { change: ScheduleChange }) {
  const d = change.detail;
  const gone = change.kind === "removed" || change.kind === "cancelled";

  return (
    <article
      className={`rounded-2xl border-2 bg-paper px-5 py-4 ${
        gone ? "border-alert" : "border-now"
      }`}
    >
      <p
        className={`text-[0.8rem] font-bold tracking-[0.1em] uppercase ${
          gone ? "text-alert" : "text-now"
        }`}
      >
        {HEADLINE[change.kind]}
      </p>

      <h2 className="mt-1 text-[1.5rem] leading-tight font-bold">
        {d.customer ?? `Job ${change.job_id}`}
      </h2>
      {d.area ? (
        <p className="text-[1rem] text-ink-soft">{d.area}</p>
      ) : null}

      {/* The one line that changes what they do next. */}
      <p className="mt-3 text-[1.05rem] leading-snug font-medium">
        {sentence(change)}
      </p>
    </article>
  );
}

function sentence(change: ScheduleChange): string {
  const d = change.detail;
  const was = d.previous_arrive ? hhmm(d.previous_arrive) : null;
  const now = d.new_arrive ? hhmm(d.new_arrive) : null;

  switch (change.kind) {
    case "removed":
      return d.moved_to
        ? `${d.moved_to} is going instead${was ? `. You were down for ${was}.` : "."}`
        : `Nobody is going for now${was ? `. You were down for ${was}.` : "."}`;
    case "cancelled":
      return was
        ? `The customer cancelled. You were down for ${was}.`
        : "The customer cancelled.";
    case "assigned":
      return d.moved_from
        ? `Moved to you from ${d.moved_from}${now ? `, now ${now}.` : "."}`
        : `Added to your day${now ? ` at ${now}` : ""}.`;
    case "retimed":
      return was && now ? `${was} is now ${now}.` : "The time moved.";
  }
}

/**
 * Every retime, as one card.
 *
 * A list of times, not a stack of cards. Individually none of these changes
 * what a technician does next -- they matter in aggregate, as "the shape of
 * my afternoon moved" -- and giving each one the same weight as a
 * reassignment is what turns this screen into something people swipe past.
 */
function RetimeCard({ changes }: { changes: ScheduleChange[] }) {
  return (
    <article className="rounded-2xl border border-line bg-paper px-5 py-4">
      <p className="text-[0.8rem] font-bold tracking-[0.1em] text-ink-soft uppercase">
        {changes.length} time{changes.length === 1 ? "" : "s"} moved
      </p>
      <ul className="mt-2 flex flex-col gap-1.5">
        {changes.map((c) => (
          <li key={c.id} className="flex items-baseline gap-3 text-[1.05rem]">
            <span className="font-bold tabular-nums">
              {c.detail.new_arrive ? hhmm(c.detail.new_arrive) : "--:--"}
            </span>
            <span className="min-w-0 flex-1 truncate font-medium">
              {c.detail.customer ?? `Job ${c.job_id}`}
            </span>
            <span className="text-[0.95rem] text-ink-soft tabular-nums">
              was{" "}
              {c.detail.previous_arrive ? hhmm(c.detail.previous_arrive) : "--:--"}
            </span>
          </li>
        ))}
      </ul>
    </article>
  );
}

/**
 * A write that will never send.
 *
 * Nearly always the other side of a reassignment: the technician marked a job
 * done in a dead zone, and by the time the phone found signal the job was
 * somebody else's, so the server answered 404. Phase 6 recorded that and
 * showed a count; this is the sentence.
 */
function FailureCard({ item }: { item: OutboxItem }) {
  const what =
    item.kind === "completion"
      ? "what you filled in"
      : item.kind === "status"
        ? `the "${item.payload.status.replace("_", " ")}" update`
        : "an acknowledgement";

  return (
    <article className="rounded-2xl border-2 border-alert bg-paper px-5 py-4">
      <p className="text-[0.8rem] font-bold tracking-[0.1em] text-alert uppercase">
        Didn&rsquo;t save
      </p>
      <h2 className="mt-1 text-[1.5rem] leading-tight font-bold">
        Job {item.jobId}
      </h2>
      <p className="mt-3 text-[1.05rem] leading-snug font-medium">
        {item.failedStatus === 404
          ? `This job isn't yours any more, so ${what} couldn't be saved. Tell dispatch if you did the work.`
          : `${what.charAt(0).toUpperCase()}${what.slice(1)} couldn't be saved. Tell dispatch.`}
      </p>
    </article>
  );
}
