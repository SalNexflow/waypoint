"use client";

import Link from "next/link";
import { useState } from "react";
import { hhmm } from "@/lib/format";
import type { FieldJob } from "@/lib/api";

/**
 * Completed jobs, collapsed to a single row.
 *
 * The spec says completed jobs collapse; this collapses them *away* rather
 * than shrinking them in place. Scrolling past four small done rows to reach
 * the job you are actually driving to is the exact friction the app exists to
 * remove. Kept at the top rather than the bottom so the screen still reads as
 * a timeline -- one thin row costs nothing and the day stays in order.
 */
export function DoneGroup({ jobs }: { jobs: FieldJob[] }) {
  const [open, setOpen] = useState(false);
  if (jobs.length === 0) return null;

  const first = jobs[0];
  const last = jobs[jobs.length - 1];

  return (
    <section>
      <button
        data-tappable
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        className="flex min-h-[3.25rem] w-full items-center gap-3 rounded-xl border border-line bg-paper px-4 text-left"
      >
        <span aria-hidden className="size-3 shrink-0 rounded-full bg-done" />
        <span className="text-[1rem] font-semibold">{jobs.length} done</span>
        <span className="text-[0.9rem] text-ink-soft tabular-nums">
          {hhmm(first.arrive)}–{hhmm(last.depart)}
        </span>
        <span aria-hidden className="ml-auto text-[0.9rem] text-ink-soft">
          {open ? "Hide" : "Show"}
        </span>
      </button>

      {open ? (
        <ul className="mt-2 flex flex-col gap-2">
          {jobs.map((job) => (
            <li key={job.id}>
              {/* Still openable. A finished job is exactly what you need the
                  address and phone number for when the customer rings back
                  an hour later. */}
              <Link
                data-tappable
                href={`/job?id=${job.id}`}
                className="flex min-h-[3.25rem] items-center gap-4 rounded-xl border border-line bg-paper px-4 py-2"
              >
                <span className="w-[3.4rem] shrink-0 text-[1rem] font-semibold tabular-nums text-ink-soft">
                  {hhmm(job.arrive)}
                </span>
                <span className="min-w-0 flex-1 truncate text-[1rem] font-medium">
                  {job.customer}
                </span>
                <span aria-hidden className="size-3 shrink-0 rounded-full bg-done" />
              </Link>
            </li>
          ))}
        </ul>
      ) : null}
    </section>
  );
}
