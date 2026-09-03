"use client";

import { useState } from "react";

import { CurrentJob } from "@/components/CurrentJob";
import { DayHeader } from "@/components/DayHeader";
import { DoneGroup } from "@/components/DoneGroup";
import { JobRow } from "@/components/JobRow";
import { ScheduleChanged } from "@/components/ScheduleChanged";
import { SyncStrip } from "@/components/SyncStrip";
import { useDay } from "@/lib/use-day";

/**
 * Today, against real data.
 *
 * The load order is the point. The technician's name comes out of
 * localStorage synchronously and the header renders immediately; the day is
 * fetched after. Nothing about who you are waits on the network.
 *
 * The jobs no longer wait either. useDay() reads the last known day out of
 * localStorage synchronously and paints it on the first render, merges
 * anything still sitting in the outbox over the top, and asks the network in
 * the background. With the radio off this screen is complete and correct
 * within one frame; the only difference is a line saying how old it is.
 */
export function TodayScreen() {
  const {
    auth,
    state,
    technicianName,
    sync,
    reload,
    signOut,
    signOutBlockedReason,
    changes,
    failures,
    interrupting,
    acknowledgeAll,
  } = useDay();

  // Retimes do not take the screen, but they are still one tap away. This is
  // what that tap opens -- the same screen, on purpose: there is no second
  // way of saying "your day changed", only a second way of getting there.
  const [showQuiet, setShowQuiet] = useState(false);

  if (auth !== "in") {
    // Not a spinner -- there is nothing to wait for. Just the wordmark, so
    // the frame before mount is not a white flash.
    return (
      <main className="mx-auto flex min-h-dvh w-full max-w-md items-center justify-center">
        <p className="text-[1.5rem] font-bold text-ink-soft">Waypoint</p>
      </main>
    );
  }

  // Before anything else. It is not a layer over the day, it is instead of
  // the day -- a technician must not be able to read around it and drive to
  // an address that is no longer theirs.
  if (interrupting || (showQuiet && changes.length > 0)) {
    return (
      <ScheduleChanged
        changes={changes}
        failures={failures}
        onAcknowledge={() => {
          setShowQuiet(false);
          acknowledgeAll();
        }}
      />
    );
  }

  if (state.kind === "waiting") {
    return <Skeleton name={technicianName} />;
  }

  if (state.kind === "failed") {
    // Only reachable with NOTHING cached -- a first run, or a first run of a
    // new day. Once there is a cached day, a failed refresh keeps it on
    // screen and shows its age instead.
    return (
      <Message
        name={technicianName}
        title={state.offline ? "No connection" : "Can't reach dispatch"}
        body={
          state.offline
            ? "Your day will load when you have signal."
            : "The server did not answer. Try again in a moment."
        }
        onRetry={reload}
        onSignOut={signOut}
        signOutBlockedReason={signOutBlockedReason}
      />
    );
  }

  const { day } = state;
  const done = day.jobs.filter((j) => j.status === "complete");
  const active = day.jobs.filter((j) => j.status !== "complete");

  // The current job is simply the first one not finished.
  //
  // Not "the job whose window contains now" -- that reads better at 2pm today,
  // when nothing can be marked done, but it is the wrong rule. Once phase 5
  // lands, completing a job is what advances this, and a clock-based
  // heuristic would then fight the technician's own actions.
  const current = active[0];
  const later = active.slice(1);

  return (
    <main className="mx-auto flex min-h-dvh w-full max-w-md flex-col">
      <DayHeader day={day} technicianName={technicianName} />
      <SyncStrip
        sync={sync}
        fetchedAt={state.fetchedAt}
        stale={state.stale}
        quietChanges={changes.length}
        onShowChanges={() => setShowQuiet(true)}
      />

      <div className="flex flex-col gap-3 px-4 pt-3 pb-8">
        <DoneGroup jobs={done} />

        {current ? (
          <CurrentJob job={current} />
        ) : (
          <p className="rounded-2xl border border-line bg-paper px-5 py-8 text-center text-[1.15rem] font-semibold">
            {day.jobs.length === 0
              ? "Nothing scheduled today."
              : "That’s the day. Nothing left."}
          </p>
        )}

        {later.length > 0 ? (
          <section className="flex flex-col gap-2">
            <h2 className="px-1 pt-2 text-[0.8rem] font-bold tracking-[0.08em] text-ink-soft uppercase">
              Then
            </h2>
            {later.map((job) => (
              <JobRow key={job.id} job={job} />
            ))}
          </section>
        ) : null}

        <Footer
          onRefresh={reload}
          onSignOut={signOut}
          signOutBlockedReason={signOutBlockedReason}
        />
      </div>
    </main>
  );
}

function Skeleton({ name }: { name: string }) {
  return (
    <main className="mx-auto flex min-h-dvh w-full max-w-md flex-col">
      <header className="border-b border-line bg-paper px-4 pt-4 pb-3">
        {name ? (
          <p className="text-[0.8rem] font-semibold tracking-[0.08em] text-ink-soft uppercase">
            {name}
          </p>
        ) : null}
        <p className="mt-1 text-[1.15rem] font-semibold text-ink-soft">
          Loading your day…
        </p>
      </header>
      {/* Blocked-out shapes at the real sizes rather than a spinner: the
          layout does not jump when the day lands. */}
      <div className="flex flex-col gap-3 px-4 pt-3">
        <div className="h-[9.5rem] rounded-2xl bg-paper" />
        <div className="h-[4.25rem] rounded-xl bg-paper" />
        <div className="h-[4.25rem] rounded-xl bg-paper" />
      </div>
    </main>
  );
}

function Message({
  name,
  title,
  body,
  onRetry,
  onSignOut,
  signOutBlockedReason,
}: {
  name: string;
  title: string;
  body: string;
  onRetry: () => void;
  onSignOut: () => void;
  signOutBlockedReason: string | null;
}) {
  return (
    <main className="mx-auto flex min-h-dvh w-full max-w-md flex-col">
      <header className="border-b border-line bg-paper px-4 pt-4 pb-3">
        {name ? (
          <p className="text-[0.8rem] font-semibold tracking-[0.08em] text-ink-soft uppercase">
            {name}
          </p>
        ) : null}
      </header>
      <div className="flex flex-col gap-3 px-4 pt-6">
        <div className="rounded-2xl border border-line bg-paper px-5 py-7">
          <p className="text-[1.3rem] font-bold">{title}</p>
          <p className="mt-2 text-[1rem] text-ink-soft">{body}</p>
          <button
            data-tappable
            type="button"
            onClick={onRetry}
            className="mt-5 min-h-[3.75rem] w-full rounded-xl bg-now text-[1.1rem] font-bold text-now-ink"
          >
            Try again
          </button>
        </div>
        <Footer onSignOut={onSignOut} signOutBlockedReason={signOutBlockedReason} />
      </div>
    </main>
  );
}

function Footer({
  onRefresh,
  onSignOut,
  signOutBlockedReason,
}: {
  onRefresh?: () => void;
  onSignOut: () => void;
  signOutBlockedReason: string | null;
}) {
  return (
    <div className="mt-6 flex flex-col items-center gap-2">
      <div className="flex items-center justify-center gap-6">
        {onRefresh ? (
          <button
            data-tappable
            type="button"
            onClick={onRefresh}
            className="min-h-[3rem] rounded-lg px-4 text-[0.95rem] font-semibold text-ink-soft underline underline-offset-4"
          >
            Refresh
          </button>
        ) : null}
        {/* The only setting there is. Past everything they actually came for,
            and not styled to invite a stray tap.

            DISABLED while anything is unsent. Signing out clears the token,
            and the queue cannot send without one -- so this button would
            quietly destroy work the technician believes is saved. Refusing is
            the difference between "log out" and "throw away the last hour". */}
        <button
          data-tappable
          type="button"
          onClick={onSignOut}
          disabled={signOutBlockedReason !== null}
          className="min-h-[3rem] rounded-lg px-4 text-[0.95rem] font-semibold text-ink-soft underline underline-offset-4 disabled:no-underline disabled:opacity-40"
        >
          Sign out
        </button>
      </div>
      {signOutBlockedReason ? (
        <p className="text-[0.85rem] text-ink-soft">
          Can&rsquo;t sign out: {signOutBlockedReason}
        </p>
      ) : null}
    </div>
  );
}
