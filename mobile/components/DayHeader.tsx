import { hhmm } from "@/lib/format";
import type { FieldDay } from "@/lib/api";

/**
 * The one line of context, at the top of Today.
 *
 * The finish estimate is the number they actually came for, so it gets the
 * weight. It comes from the server (`finish_estimate`) rather than being
 * derived here, so there is one definition of it -- currently the last
 * assignment's predicted departure, which is a solver prediction and stops
 * being true the moment the day slips. Phase 9 should recompute it forward
 * from the latest real status event; until then the word "around" is
 * carrying a lot.
 *
 * The name comes from the session, not from the day: identity is what the
 * token proves, and it renders from cache before any request is made.
 */
export function DayHeader({
  day,
  technicianName,
}: {
  day: FieldDay;
  technicianName: string;
}) {
  const remaining = day.jobs.filter((j) => j.status !== "complete").length;

  return (
    <header className="sticky top-0 z-10 border-b border-line bg-paper px-4 pt-4 pb-3">
      {/* Empty only in the narrow case of a stored token with no cached
          name -- one paint, until the day arrives. Rendering nothing beats
          rendering a placeholder that looks like somebody's name. */}
      {technicianName ? (
        <p className="text-[0.8rem] font-semibold tracking-[0.08em] text-ink-soft uppercase">
          {technicianName}
        </p>
      ) : null}
      <p className="mt-1 text-[1.15rem] leading-tight font-semibold">
        {remaining} {remaining === 1 ? "job" : "jobs"} left
        {day.finish_estimate ? (
          <>
            <span className="text-ink-soft"> · </span>
            finish around{" "}
            <span className="tabular-nums">{hhmm(day.finish_estimate)}</span>
          </>
        ) : null}
      </p>
    </header>
  );
}
