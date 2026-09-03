import Link from "next/link";
import { hhmm } from "@/lib/format";
import type { FieldJob } from "@/lib/api";

const DOT: Record<string, string> = {
  upcoming: "border-2 border-ink-soft",
  en_route: "bg-now",
  arrived: "bg-now",
  complete: "bg-done",
};

/**
 * One later job. Smaller than the current card, same contrast.
 *
 * min-h-[4.25rem] is a touch-target floor, not a look: roughly 72px, which
 * clears the ~9mm a gloved fingertip needs. Every tappable row in this app
 * holds that floor.
 *
 * A Link, not a div with an onClick. The whole row is the target -- a phone
 * screen has no cursor to aim with -- and a real anchor gets the browser's
 * own handling of long-press, back navigation and keyboard focus for free.
 */
export function JobRow({ job }: { job: FieldJob }) {
  return (
    <Link
      data-tappable
      href={`/job?id=${job.id}`}
      className="flex min-h-[4.25rem] items-center gap-4 rounded-xl border border-line bg-paper px-4 py-3"
    >
      <span className="w-[3.4rem] shrink-0 text-[1.05rem] font-bold tabular-nums">
        {hhmm(job.arrive)}
      </span>
      <span className="min-w-0 flex-1">
        <span className="block truncate text-[1.05rem] leading-snug font-semibold">
          {job.customer}
        </span>
        {job.area ? (
          <span className="block text-[0.9rem] text-ink-soft">{job.area}</span>
        ) : null}
      </span>
      <span
        aria-hidden
        className={`size-3 shrink-0 rounded-full ${DOT[job.status] ?? DOT.upcoming}`}
      />
    </Link>
  );
}
