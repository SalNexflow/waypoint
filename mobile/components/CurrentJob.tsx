import Link from "next/link";
import { duration, hhmm } from "@/lib/format";
import type { FieldJob } from "@/lib/api";
import { STATUS_LABEL } from "@/lib/status";

/**
 * The current job: the largest thing on the screen, and the only one that
 * needs to be readable at arm's length.
 *
 * Dominance comes from size, colour and position -- not from dimming what is
 * around it. In direct sunlight a dimmed row is not de-emphasised, it is
 * invisible, which is why the rest of the list stays at full contrast.
 *
 * The whole card is the tap target for job detail. There is no separate
 * "open" affordance, because on this screen there is nothing else it could
 * mean.
 */
export function CurrentJob({ job }: { job: FieldJob }) {
  return (
    <Link
      data-tappable
      href={`/job?id=${job.id}`}
      className="block rounded-2xl bg-now px-5 pt-4 pb-5 text-now-ink shadow-sm"
    >
      <div className="flex items-baseline justify-between">
        <span className="text-[0.8rem] font-bold tracking-[0.1em] uppercase">
          {job.status === "upcoming" ? "Next" : STATUS_LABEL[job.status]}
        </span>
        <span className="text-[1.05rem] font-semibold tabular-nums">
          {hhmm(job.arrive)}
        </span>
      </div>

      <h2 className="mt-2 text-[1.75rem] leading-[1.15] font-bold">
        {job.customer}
      </h2>

      <p className="mt-1.5 text-[1rem] font-medium">
        {job.area ? (
          <>
            {job.area}
            <span className="opacity-80"> · </span>
          </>
        ) : null}
        {duration(job.duration_seconds)}
      </p>

      {job.service_type ? (
        <p className="mt-3 border-t border-white/25 pt-3 text-[0.95rem]">
          {job.service_type}
        </p>
      ) : null}
    </Link>
  );
}
