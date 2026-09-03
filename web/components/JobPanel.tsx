"use client";

import { Route, Unassigned, Visit, colourFor } from "@/lib/api";

interface Props {
  routes: Route[];
  unassigned: Unassigned[];
  selectedJob: number | null;
  onClose: () => void;
}

/** Detail for one job, or the list of everything that did not fit. */
export default function JobPanel({
  routes,
  unassigned,
  selectedJob,
  onClose,
}: Props) {
  type Found = { visit: Visit; route: Route; index: number };
  let found: Found | null = null;
  routes.forEach((route, index) => {
    const visit = route.visits.find((v) => v.job_id === selectedJob);
    if (visit) found = { visit, route, index } satisfies Found;
  });

  if (found !== null) {
    const { visit, route, index } = found as Found;
    return (
      <aside className="panel">
        <header>
          <h3>
            <span className="swatch" style={{ background: colourFor(index) }} />
            {visit.job_ref} {visit.customer}
          </h3>
          <button onClick={onClose} aria-label="close">×</button>
        </header>
        <dl>
          <dt>technician</dt><dd>{route.technician_name}</dd>
          <dt>stop</dt><dd>#{visit.sequence + 1} of {route.visits.length}</dd>
          <dt>arrive</dt><dd>{visit.arrive}</dd>
          <dt>work</dt><dd>{visit.start} – {visit.end}</dd>
          {visit.wait_minutes > 0 && (
            <>
              <dt>waiting</dt>
              <dd>{visit.wait_minutes} min before the window opened</dd>
            </>
          )}
          <dt>location</dt>
          <dd>{visit.lat.toFixed(5)}, {visit.lon.toFixed(5)}</dd>
        </dl>
        <p className="hint">Drag this job onto another technician in the timeline to reassign it.</p>
      </aside>
    );
  }

  if (unassigned.length === 0) return null;

  return (
    <aside className="panel">
      <header>
        <h3>{unassigned.length} unassigned</h3>
        <button onClick={onClose} aria-label="close">×</button>
      </header>
      <ul className="unassigned">
        {unassigned.map((u) => (
          <li key={u.job_ref}>
            <strong>{u.job_ref}</strong> {u.customer}
            <span className="reason">{u.reason.replace(/_/g, " ")}</span>
            <p>{u.message}</p>
          </li>
        ))}
      </ul>
    </aside>
  );
}
