"use client";

import { useCallback, useEffect, useState } from "react";
import dynamic from "next/dynamic";
import Link from "next/link";
import {
  LockedError,
  ReassignPreview,
  SolveMetrics,
  SolveResult,
  api,
} from "@/lib/api";
import Unlock from "@/components/Unlock";
import Timeline from "@/components/Timeline";
import JobPanel from "@/components/JobPanel";
import SolveStatus from "@/components/SolveStatus";
import DispatchBar from "@/components/DispatchBar";

// MapLibre touches `window` on import, so it cannot be server-rendered.
const DayMap = dynamic(() => import("@/components/DayMap"), {
  ssr: false,
  loading: () => <div className="map-loading">loading map…</div>,
});

// The dispatch day, in the timezone the dispatch day is defined in.
//
// Not the browser's date: a dispatcher in another timezone would otherwise
// open on a day the API does not consider today, see an empty board, and
// conclude the schedule had been lost. And not `new Date()` rendered
// directly either -- this component server-renders before it hydrates, so a
// value that depends on where the code is running produces a hydration
// mismatch. Pinning the zone makes both sides compute the same string.
const DISPATCH_TZ = "Asia/Kuala_Lumpur";

function dispatchToday(now: Date = new Date()): string {
  // en-CA formats as YYYY-MM-DD, which is what every date input and every
  // API route here expects.
  return new Intl.DateTimeFormat("en-CA", {
    timeZone: DISPATCH_TZ,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).format(now);
}

export default function Page() {
  const [day, setDay] = useState(dispatchToday);
  const [result, setResult] = useState<SolveResult | null>(null);
  const [previous, setPrevious] = useState<SolveMetrics | null>(null);
  const [selectedTech, setSelectedTech] = useState<string | null>(null);
  const [selectedJob, setSelectedJob] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [locked, setLocked] = useState<LockedError | null>(null);
  const [busy, setBusy] = useState(false);
  const [solving, setSolving] = useState(false);
  const [reassign, setReassign] = useState<ReassignPreview | null>(null);
  const [pendingDrop, setPendingDrop] = useState<
    { jobId: number; techId: number } | null
  >(null);

  const load = useCallback(async () => {
    try {
      const r = await api.latest(day);
      setResult((prev) => {
        if (prev) setPrevious(prev.metrics);
        return r;
      });
      setError(null);
    } catch (e) {
      // A locked API is not "no schedule for this day". Showing that would
      // send a dispatcher off to re-seed a database that is perfectly fine.
      if (e instanceof LockedError) {
        setLocked(e);
        return;
      }
      setResult(null);
      setError(
        `No solved schedule for ${day}. Seed a day and press Solve. (${e})`,
      );
    }
  }, [day]);

  useEffect(() => {
    load();
  }, [load]);

  async function runSolve() {
    setSolving(true);
    setError(null);
    try {
      const run = await api.solve(day, 30, 8);
      // Poll rather than hold the request open: a solve takes tens of seconds
      // and the run row is the authoritative status.
      for (let i = 0; i < 120; i++) {
        await new Promise((r) => setTimeout(r, 2000));
        const status = await api.run(run.id);
        if (status.status === "succeeded") {
          await load();
          break;
        }
        if (status.status === "failed") {
          setError(status.error ?? "solve failed");
          break;
        }
      }
    } catch (e) {
      setError(String(e));
    } finally {
      setSolving(false);
    }
  }

  async function onDrop(jobId: number, techId: number) {
    if (!result?.run_id) return;
    setBusy(true);
    setReassign(null);
    setPendingDrop({ jobId, techId });
    try {
      const preview = await api.reassign(result.run_id, jobId, techId, false);
      setReassign(preview);
    } catch (e) {
      setError(String(e));
      setPendingDrop(null);
    } finally {
      setBusy(false);
    }
  }

  async function commitReassign() {
    if (!result?.run_id || !pendingDrop) return;
    setBusy(true);
    try {
      await api.reassign(
        result.run_id,
        pendingDrop.jobId,
        pendingDrop.techId,
        true,
      );
      setReassign(null);
      setPendingDrop(null);
      await load();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }

  if (locked) return <Unlock reason={locked.message} status={locked.status} />;

  return (
    <main className="shell">
      <header className="topbar">
        <h1>Waypoint</h1>
        <input
          type="date"
          value={day}
          onChange={(e) => setDay(e.target.value)}
        />
        <button onClick={runSolve} disabled={solving}>
          {solving ? "solving…" : "Solve"}
        </button>
        <button className="ghost" onClick={load} disabled={solving}>
          Refresh
        </button>
        {result && (
          <SolveStatus
            metrics={result.metrics}
            previous={previous}
            runId={result.run_id}
          />
        )}
        {/* The only change this page needed for the field app: a way to
            reach the access screen. The solve UI is otherwise untouched. */}
        <Link className="navlink" href="/access">
          Technician access →
        </Link>
      </header>

      {error && <p className="banner error">{error}</p>}

      {result && !result.metrics.valid && (
        <p className="banner error">
          The independent checker rejected this schedule:{" "}
          {result.metrics.violations.slice(0, 2).join("; ")}
        </p>
      )}

      <DispatchBar day={day} runId={result?.run_id ?? null} onCommitted={load} />

      {reassign && (
        <div className={`banner ${reassign.ok ? "preview" : "error"}`}>
          {reassign.ok ? (
            <>
              <strong>Move preview:</strong> driving{" "}
              {reassign.travel_delta_minutes > 0 ? "+" : ""}
              {reassign.travel_delta_minutes}m, unassigned{" "}
              {reassign.unassigned_delta > 0 ? "+" : ""}
              {reassign.unassigned_delta}, {reassign.customer_calls} customer
              call(s){reassign.valid ? "" : " — CHECKER REJECTED"}
              {reassign.calls.length > 0 && (
                <ul className="calls">
                  {reassign.calls.map((c) => (
                    <li key={c}>{c}</li>
                  ))}
                </ul>
              )}
              <button onClick={commitReassign} disabled={busy || !reassign.valid}>
                Apply
              </button>
              <button
                className="ghost"
                onClick={() => {
                  setReassign(null);
                  setPendingDrop(null);
                }}
              >
                Cancel
              </button>
            </>
          ) : (
            <>
              <strong>Cannot move that job:</strong> {reassign.reason}
              <button className="ghost" onClick={() => setReassign(null)}>
                Dismiss
              </button>
            </>
          )}
        </div>
      )}

      <section className="body">
        <div className="map">
          <DayMap
            routes={result?.routes ?? []}
            unassigned={result?.unassigned ?? []}
            selectedTech={selectedTech}
            onSelectJob={(jobId) => setSelectedJob(jobId)}
          />
        </div>

        {(selectedJob !== null ||
          (result?.unassigned.length ?? 0) > 0) && (
          <JobPanel
            routes={result?.routes ?? []}
            unassigned={result?.unassigned ?? []}
            selectedJob={selectedJob}
            onClose={() => setSelectedJob(null)}
          />
        )}
      </section>

      <footer className="bottom">
        {result ? (
          <Timeline
            routes={result.routes}
            selectedTech={selectedTech}
            onSelectTech={setSelectedTech}
            onDrop={onDrop}
            busy={busy}
          />
        ) : (
          <p className="empty">No schedule loaded.</p>
        )}
      </footer>
    </main>
  );
}
