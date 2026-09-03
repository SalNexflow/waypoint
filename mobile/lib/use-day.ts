"use client";

import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import {
  ApiError,
  type FieldDay,
  type JobStatus,
  INTERRUPTING,
  type ScheduleChange,
  type TechnicianMe,
  fetchChanges,
  fetchToday,
} from "@/lib/api";
import { loadDay, localDay, saveDay } from "@/lib/day-cache";
import {
  type OutboxItem,
  enqueue,
  failedItems,
  markSeen,
  pendingItems,
  queuedAcks,
  queuedStatuses,
} from "@/lib/outbox";
import {
  cacheTechnician,
  clearSession,
  getCachedTechnician,
  getToken,
} from "@/lib/session";
import { type SyncState, startSyncing, subscribeSync, syncNow } from "@/lib/sync";

/**
 * The technician's day: what the server last said, plus what they have done
 * since, whether or not any of it has sent.
 *
 * THE LOAD ORDER, WHICH IS THE POINT
 * ----------------------------------
 *   1. The cached day is read from localStorage SYNCHRONOUSLY, in the state
 *      initialiser, and painted on the first render. No await, no skeleton,
 *      no spinner -- the app opens and shows the next job even with the radio
 *      off. This is why the cache is localStorage and the queue is IndexedDB:
 *      one is chosen for being fast to read, the other for being impossible
 *      to lose.
 *   2. Queued work is merged over it, so a status the technician tapped in a
 *      basement is on screen before anything has been sent.
 *   3. The network is asked in the background, and only replaces what is on
 *      screen once it answers.
 *
 * A failure at step 3 is not an error state any more. It is a marker saying
 * how old what you are looking at is.
 */

export type DayState =
  | { kind: "waiting" }
  | { kind: "ready"; day: FieldDay; fetchedAt: string | null; stale: boolean }
  | { kind: "failed"; offline: boolean };

export interface Day {
  auth: "unknown" | "in" | "out";
  state: DayState;
  technicianName: string;
  sync: SyncState;
  reload: () => void;
  /** Re-read the outbox and re-merge, without touching the network. */
  refreshLocal: () => void;
  /** Unacknowledged changes, minus anything acknowledged locally. */
  changes: ScheduleChange[];
  /** Writes that will never send, waiting to be explained. */
  failures: OutboxItem[];
  /** True when the changes are the kind that take over the screen. */
  interrupting: boolean;
  acknowledgeAll: () => void;
  signOut: () => void;
  /** Non-null when signing out would destroy unsent work. */
  signOutBlockedReason: string | null;
}

const RANK: Record<string, number> = {
  upcoming: 0,
  en_route: 1,
  arrived: 2,
  complete: 3,
};

/** Apply local statuses over the server's, taking whichever got furthest. */
function merge(day: FieldDay, local: Map<number, string>): FieldDay {
  if (local.size === 0) return day;
  return {
    ...day,
    jobs: day.jobs.map((job) => {
      const mine = local.get(job.id) as JobStatus | undefined;
      if (!mine) return job;
      return RANK[mine] > RANK[job.status] ? { ...job, status: mine } : job;
    }),
  };
}

/** Fold new statuses into the running local maximum. */
function absorb(
  prev: Map<number, string>,
  incoming: Map<number, string>,
): Map<number, string> {
  const next = new Map(prev);
  for (const [jobId, status] of incoming) {
    const current = next.get(jobId);
    if (!current || RANK[status] > RANK[current]) next.set(jobId, status);
  }
  return next;
}

export function useDay(): Day {
  const router = useRouter();

  const [auth, setAuth] = useState<"unknown" | "in" | "out">("unknown");
  const [technician, setTechnician] = useState<TechnicianMe | null>(null);
  const [state, setState] = useState<DayState>({ kind: "waiting" });
  const [local, setLocal] = useState<Map<number, string>>(new Map());
  const [changes, setChanges] = useState<ScheduleChange[]>([]);
  const [acked, setAcked] = useState<Set<number>>(new Set());
  const [failures, setFailures] = useState<OutboxItem[]>([]);
  const [sync, setSync] = useState<SyncState>({
    pending: 0,
    failed: 0,
    syncing: false,
    online: true,
    lastSyncAt: null,
  });

  // The running maximum of everything this technician has done, this session.
  //
  // ACCUMULATED, not replaced. The obvious version -- overwrite the map from
  // whatever is currently in the outbox -- flickers: the moment an item drains
  // successfully it leaves the outbox, and until the refetched day comes back
  // the merge falls through to the cached day, which still says "upcoming".
  // The technician watches a job they finished revert for half a second.
  //
  // A running max cannot regress. Once the server catches up its own status is
  // equal or higher, so the overlay quietly stops mattering rather than being
  // cleared at a moment that has to be timed correctly.
  const refreshLocal = useCallback(() => {
    void pendingItems().then((items) => {
      setLocal((prev) => absorb(prev, queuedStatuses(items)));
      // Acks queued but not yet sent still count as acknowledged HERE, or
      // tapping "Got it" in a basement would dismiss the interrupt until the
      // next refresh and then have it reappear.
      setAcked((prev) => new Set([...prev, ...queuedAcks(items)]));
    });
    void failedItems().then(setFailures);
  }, []);

  const signOut = useCallback(() => {
    clearSession();
    setAuth("out");
    router.replace("/login");
  }, [router]);

  const reload = useCallback(async () => {
    const token = getToken();
    if (!token) {
      signOut();
      return;
    }
    try {
      const day = await fetchToday(token);
      saveDay(day);
      setState({ kind: "ready", day, fetchedAt: new Date().toISOString(), stale: false });

      const me: TechnicianMe = {
        id: day.technician_id,
        name: day.technician_name,
        shift_start: "",
        shift_end: "",
      };
      setTechnician((prev) => (prev?.name === me.name ? prev : me));
      cacheTechnician(me);
      // Fetched alongside the day rather than on a timer of its own: the
      // moments worth checking for a change are exactly the moments worth
      // refreshing the day, and two schedules would drift apart.
      try {
        setChanges(await fetchChanges(token));
      } catch {
        // A change nobody could fetch is not an error worth a screen. The
        // cached day is still right and the next refresh will bring them.
      }
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) {
        signOut();
        return;
      }
      // Keep whatever is on screen and mark it old. Replacing a usable day
      // with an error page because a refresh failed is the opposite of what
      // an offline-first app should do.
      setState((prev) =>
        prev.kind === "ready"
          ? { ...prev, stale: true }
          : { kind: "failed", offline: !(err instanceof ApiError) },
      );
    }
  }, [signOut]);

  useEffect(() => {
    if (!getToken()) {
      setAuth("out");
      router.replace("/login");
      return;
    }
    setAuth("in");
    const me = getCachedTechnician();
    setTechnician(me);

    // Synchronous. This is the first paint.
    const cached = loadDay(localDay(), me?.id ?? null);
    if (cached) {
      setState({
        kind: "ready",
        day: cached.day,
        fetchedAt: cached.fetchedAt,
        // Anything from a previous session is old until proven otherwise.
        stale: true,
      });
    }

    refreshLocal();
    startSyncing();
    void reload();
  }, [router, reload, refreshLocal]);

  useEffect(() => subscribeSync(setSync), []);

  // Any change to the queue -- an item added, an item drained -- means the
  // overlay may have moved. Deliberately NOT `if (pending === 0)`: that only
  // fires when the queue empties, so a status tapped in a dead zone (0 -> 1)
  // never reached the screen at all. That was a real bug, and airplane mode
  // is what found it.
  useEffect(() => {
    refreshLocal();
  }, [sync.pending, sync.failed, refreshLocal]);

  useEffect(() => {
    if (sync.lastSyncAt) void reload();
  }, [sync.lastSyncAt, reload]);

  const merged: DayState =
    state.kind === "ready"
      ? { ...state, day: merge(state.day, local) }
      : state;

  const outstanding = changes.filter((c) => !acked.has(c.id));
  const unseenFailures = failures.filter((f) => f.state === "failed");

  const acknowledgeAll = useCallback(() => {
    // Optimistic and durable in the same breath. The ids are hidden
    // immediately so the screen closes on the tap, and each ack goes through
    // the outbox so it survives a dead zone like every other write.
    setAcked((prev) => new Set([...prev, ...outstanding.map((c) => c.id)]));
    void (async () => {
      for (const change of outstanding) {
        await enqueue({
          kind: "ack",
          jobId: change.job_id,
          payload: { changeId: change.id },
        });
      }
      // Failures have now been explained; stop counting them so the warning
      // strip can clear. The rows stay in IndexedDB as evidence.
      for (const item of unseenFailures) await markSeen(item.seq);
      refreshLocal();
      void syncNow();
    })();
  }, [outstanding, unseenFailures, refreshLocal]);

  return {
    auth,
    state: merged,
    technicianName: technician?.name ?? "",
    sync,
    reload: () => void reload(),
    refreshLocal,
    changes: outstanding,
    failures: unseenFailures,
    // The spec's trigger, exactly: "when dispatch reassigns or cancels
    // something belonging to this technician". A retime on its own is
    // recorded and shown, but never takes the screen.
    interrupting:
      outstanding.some((c) => INTERRUPTING.has(c.kind)) ||
      unseenFailures.length > 0,
    acknowledgeAll,
    signOut,
    // Signing out clears the token, and the queue cannot send without one.
    // Refusing while anything is unsent is the difference between "log out"
    // and "throw away the last hour of work".
    signOutBlockedReason:
      sync.pending > 0
        ? `${sync.pending} update${sync.pending === 1 ? "" : "s"} still to send`
        : null,
  };
}

/** Kick a sync. Exported so the action button can try immediately after a write. */
export { syncNow };
