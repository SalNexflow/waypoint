"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { ActionButton } from "@/components/ActionButton";
import type { JobStatus } from "@/lib/api";
import { duration, hhmm, partLabel } from "@/lib/format";
import { navigationUri, telUri } from "@/lib/handoff";
import { STATUS_LABEL } from "@/lib/status";
import { useDay } from "@/lib/use-day";

/**
 * Job detail -- screen 2 of 4.
 *
 * Everything a technician needs while standing outside the building, in the
 * order they need it: who and where, then when, then what the job is, then
 * anything that changes how they approach the door.
 *
 * Reads from the same `GET /field/today` payload as the Today screen, through
 * the same hook -- which is why the day carries the detail fields rather than
 * this screen having a route of its own. That one cached response is what
 * lets a technician in a basement open a job and see where they are going.
 */
export function JobScreen({ jobId }: { jobId: number | null }) {
  const router = useRouter();
  const { auth, state, reload, refreshLocal, signOut, signOutBlockedReason } =
    useDay();

  // A status applied locally, ahead of the server confirming it. Held here
  // rather than inside ActionButton so the header above reflects the tap
  // too -- pressing "Arrived" should change what the screen says about the
  // job, not just what the button says next.
  const [optimistic, setOptimistic] = useState<JobStatus | null>(null);

  if (auth !== "in" || state.kind === "waiting") {
    return (
      <main className="mx-auto flex min-h-dvh w-full max-w-md items-center justify-center">
        <p className="text-[1.5rem] font-bold text-ink-soft">Waypoint</p>
      </main>
    );
  }

  if (state.kind === "failed") {
    // Only with NOTHING cached. A failed refresh on top of a cached day keeps
    // the job on screen; the Today header is where its age is shown.
    return (
      <Shell onBack={() => router.push("/")}>
        <div className="rounded-2xl border border-line bg-paper px-5 py-7">
          <p className="text-[1.3rem] font-bold">
            {state.offline ? "No connection" : "Can't reach dispatch"}
          </p>
          <p className="mt-2 text-[1rem] text-ink-soft">
            This job will load when you have signal.
          </p>
          <button
            data-tappable
            type="button"
            onClick={reload}
            className="mt-5 min-h-[3.75rem] w-full rounded-xl bg-now text-[1.1rem] font-bold text-now-ink"
          >
            Try again
          </button>
        </div>
      </Shell>
    );
  }

  const job = state.day.jobs.find((j) => j.id === jobId);

  if (!job) {
    // Not on this technician's day. Could be a stale link, a job reassigned
    // away, or someone else's job id typed into the address bar -- and the
    // wording deliberately does not distinguish them. The API already returns
    // 404 rather than 403 for exactly this reason; saying "that job is not
    // yours" here would give away what the API refuses to confirm.
    return (
      <Shell onBack={() => router.push("/")}>
        <div className="rounded-2xl border border-line bg-paper px-5 py-7">
          <p className="text-[1.3rem] font-bold">That job isn&rsquo;t on your day</p>
          <p className="mt-2 text-[1rem] text-ink-soft">
            It may have been moved to someone else. Check Today.
          </p>
          <Link
            data-tappable
            href="/"
            className="mt-5 flex min-h-[3.75rem] w-full items-center justify-center rounded-xl bg-now text-[1.1rem] font-bold text-now-ink"
          >
            Back to today
          </Link>
        </div>
      </Shell>
    );
  }

  const tel = telUri(job);
  const navigate = navigationUri(
    job,
    typeof navigator === "undefined" ? "" : navigator.userAgent,
  );

  return (
    <Shell
      onBack={() => router.push("/")}
      onSignOut={signOut}
      signOutBlocked={signOutBlockedReason !== null}
    >
      {/* --- Who, and where --- */}
      <section>
        <p className="text-[0.8rem] font-bold tracking-[0.1em] text-ink-soft uppercase">
          {STATUS_LABEL[optimistic ?? job.status]}
          {job.service_type ? ` · ${job.service_type}` : ""}
        </p>
        <h1 className="mt-1 text-[1.9rem] leading-[1.15] font-bold">
          {job.customer}
        </h1>
        {job.address ? (
          <p className="mt-2 text-[1.05rem] leading-snug">{job.address}</p>
        ) : job.area ? (
          <p className="mt-2 text-[1.05rem] leading-snug">{job.area}</p>
        ) : null}
      </section>

      {/* --- Getting there and getting hold of them ---
          Above the fold and above everything descriptive, because these are
          the two things done while still in the van. */}
      <section className="flex gap-3">
        <a
          data-tappable
          href={navigate}
          className="flex min-h-[4rem] flex-1 items-center justify-center gap-2 rounded-xl bg-now text-[1.1rem] font-bold text-now-ink"
        >
          <ArrowIcon />
          Navigate
        </a>
        {tel ? (
          <a
            data-tappable
            href={tel}
            aria-label={`Call ${job.customer}`}
            className="flex min-h-[4rem] w-[4.5rem] items-center justify-center rounded-xl border-2 border-now text-now"
          >
            <PhoneIcon />
          </a>
        ) : null}
      </section>

      {/* --- When --- */}
      <Panel>
        <Row
          label={job.window_is_promise ? "Promised" : "Must be done"}
          value={`${hhmm(job.window_start)}–${hhmm(job.window_end)}`}
        />
        <Row label="Planned arrival" value={hhmm(job.arrive)} />
        <Row label="Allow" value={duration(job.duration_seconds)} />
      </Panel>

      {/* --- What --- */}
      {job.fault_description ? (
        <Panel>
          <p className="text-[0.75rem] font-bold tracking-[0.08em] text-ink-soft uppercase">
            Reported fault
          </p>
          <p className="mt-1.5 text-[1.05rem] leading-snug">
            {job.fault_description}
          </p>
        </Panel>
      ) : null}

      <Panel>
        <p className="text-[0.75rem] font-bold tracking-[0.08em] text-ink-soft uppercase">
          Parts
        </p>
        {job.parts.length > 0 ? (
          <ul className="mt-2 flex flex-wrap gap-2">
            {job.parts.map((part) => (
              <li
                key={part}
                className="rounded-lg border border-line px-3 py-1.5 text-[0.95rem] font-semibold"
              >
                {partLabel(part)}
              </li>
            ))}
          </ul>
        ) : (
          <p className="mt-1.5 text-[1.05rem]">None planned.</p>
        )}
      </Panel>

      {/* --- Anything that changes how they approach the door ---
          Last, and visually loudest of the panels. A note is rare, and when
          there is one it is always the thing that stops a wasted trip. */}
      {job.notes ? (
        <div className="rounded-xl border-2 border-now bg-paper px-4 py-3.5">
          <p className="text-[0.75rem] font-bold tracking-[0.08em] text-now uppercase">
            Note
          </p>
          <p className="mt-1.5 text-[1.05rem] leading-snug font-medium">
            {job.notes}
          </p>
        </div>
      ) : null}

      {/* Spacer so the sticky action bar never covers the last panel. */}
      <div className="h-[6.5rem]" aria-hidden />

      {/* --- The one action ---
          Everything above scrolls under it; the spacer keeps the last panel
          clear. Its behaviour lives in ActionButton because the undo window
          and the write are one concern, and phase 6 replaces both together. */}
      <ActionButton
        job={job}
        optimistic={optimistic}
        onOptimistic={setOptimistic}
        // Re-read the outbox the instant the write lands, so the status
        // on screen comes from durable storage rather than component
        // state -- and survives a reload, a screen change and a restart.
        onSettled={refreshLocal}
        onComplete={() => router.push(`/complete?id=${job.id}`)}
      />

    </Shell>
  );
}

function Shell({
  children,
  onBack,
  onSignOut,
  signOutBlocked = false,
}: {
  children: React.ReactNode;
  onBack: () => void;
  onSignOut?: () => void;
  signOutBlocked?: boolean;
}) {
  return (
    <main className="mx-auto flex min-h-dvh w-full max-w-md flex-col">
      <header className="sticky top-0 z-10 flex items-center border-b border-line bg-paper px-2 py-2">
        <button
          data-tappable
          type="button"
          onClick={onBack}
          // Router push rather than history.back(): arriving here from a
          // notification or a restored tab means there is no history to go
          // back to, and a back button that does nothing is worse than none.
          className="flex min-h-[3.25rem] items-center gap-1 rounded-lg px-3 text-[1.05rem] font-semibold"
        >
          <span aria-hidden>←</span> Today
        </button>
        {onSignOut ? (
          <button
            data-tappable
            type="button"
            onClick={onSignOut}
            // Disabled while the outbox is non-empty: signing out clears the
            // token the queue needs to send with.
            disabled={signOutBlocked}
            className="ml-auto min-h-[3.25rem] rounded-lg px-3 text-[0.9rem] font-semibold text-ink-soft underline underline-offset-4 disabled:no-underline disabled:opacity-40"
          >
            Sign out
          </button>
        ) : null}
      </header>
      <div className="flex flex-col gap-3 px-4 pt-4">{children}</div>
    </main>
  );
}

function Panel({ children }: { children: React.ReactNode }) {
  return (
    <div className="rounded-xl border border-line bg-paper px-4 py-3.5">
      {children}
    </div>
  );
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-baseline justify-between py-1">
      <span className="text-[0.95rem] text-ink-soft">{label}</span>
      <span className="text-[1.1rem] font-bold tabular-nums">{value}</span>
    </div>
  );
}

/* Inline SVG rather than an icon package: two icons do not justify a
   dependency, and these inherit currentColor so they follow the theme
   tokens without a second colour definition. */

function PhoneIcon() {
  return (
    <svg width="26" height="26" viewBox="0 0 24 24" fill="currentColor" aria-hidden>
      <path d="M6.6 10.8a15.1 15.1 0 0 0 6.6 6.6l2.2-2.2a1 1 0 0 1 1-.25 11.4 11.4 0 0 0 3.6.58 1 1 0 0 1 1 1V20a1 1 0 0 1-1 1A17 17 0 0 1 3 4a1 1 0 0 1 1-1h3.5a1 1 0 0 1 1 1c0 1.25.2 2.46.57 3.6a1 1 0 0 1-.25 1z" />
    </svg>
  );
}

function ArrowIcon() {
  return (
    <svg width="22" height="22" viewBox="0 0 24 24" fill="currentColor" aria-hidden>
      <path d="M21.4 2.6a1 1 0 0 0-1.05-.23l-17 6.5a1 1 0 0 0 .05 1.88l7.2 2.3 2.3 7.2a1 1 0 0 0 1.88.05l6.5-17a1 1 0 0 0-.23-1.05z" />
    </svg>
  );
}
