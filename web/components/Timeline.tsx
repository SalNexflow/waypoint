"use client";

import { useState } from "react";
import { Route, Visit, colourFor, hhmmToMinutes } from "@/lib/api";

interface Props {
  routes: Route[];
  selectedTech: string | null;
  onSelectTech: (ref: string | null) => void;
  onDrop: (jobId: number, technicianId: number) => void;
  busy: boolean;
}

const DAY_START = 7 * 60;
const DAY_END = 19 * 60;
const SPAN = DAY_END - DAY_START;

function pct(minutes: number): number {
  return ((minutes - DAY_START) / SPAN) * 100;
}

/**
 * Gantt per technician, with drag-to-reassign.
 *
 * Uses the HTML5 drag-and-drop API rather than pointer maths: it gives
 * keyboard-accessible semantics for free and the drop target is a whole row,
 * which is a large, forgiving hit area.
 */
export default function Timeline({
  routes,
  selectedTech,
  onSelectTech,
  onDrop,
  busy,
}: Props) {
  const [dragJob, setDragJob] = useState<number | null>(null);
  const [hoverTech, setHoverTech] = useState<string | null>(null);

  const hours = Array.from({ length: 13 }, (_, i) => 7 + i);

  return (
    <div className="timeline">
      <div className="tl-head">
        <div className="tl-name" />
        <div className="tl-track">
          {hours.map((h) => (
            <div
              key={h}
              className="tl-hour"
              style={{ left: `${pct(h * 60)}%` }}
            >
              {String(h).padStart(2, "0")}
            </div>
          ))}
        </div>
      </div>

      {routes.map((route, i) => {
        const colour = colourFor(i);
        const dim = selectedTech !== null && selectedTech !== route.technician_ref;
        const isTarget = hoverTech === route.technician_ref && dragJob !== null;

        return (
          <div
            key={route.technician_ref}
            className={`tl-row${dim ? " dim" : ""}${isTarget ? " target" : ""}`}
            onDragOver={(e) => {
              e.preventDefault();
              setHoverTech(route.technician_ref);
            }}
            onDragLeave={() => setHoverTech(null)}
            onDrop={(e) => {
              e.preventDefault();
              setHoverTech(null);
              if (dragJob !== null && !busy) {
                onDrop(dragJob, route.technician_id);
              }
              setDragJob(null);
            }}
          >
            <button
              className="tl-name"
              onClick={() =>
                onSelectTech(
                  selectedTech === route.technician_ref
                    ? null
                    : route.technician_ref,
                )
              }
              title="Click to isolate this technician on the map"
            >
              <span className="swatch" style={{ background: colour }} />
              <span className="who">
                <strong>{route.technician_name}</strong>
                <small>
                  {route.visits.length} jobs · {route.travel_minutes}m driving
                  {route.wait_minutes > 0 ? ` · ${route.wait_minutes}m idle` : ""}
                </small>
              </span>
            </button>

            <div className="tl-track">
              {/* shift envelope */}
              <div
                className="tl-shift"
                style={{
                  left: `${pct(hhmmToMinutes(route.shift_start))}%`,
                  width: `${pct(hhmmToMinutes(route.shift_end)) - pct(hhmmToMinutes(route.shift_start))}%`,
                }}
              />
              {route.visits.map((v: Visit) => {
                const start = hhmmToMinutes(v.start);
                const end = hhmmToMinutes(v.end);
                const arrive = hhmmToMinutes(v.arrive);
                return (
                  <div key={v.job_ref}>
                    {v.wait_minutes > 0 && (
                      <div
                        className="tl-wait"
                        style={{
                          left: `${pct(arrive)}%`,
                          width: `${pct(start) - pct(arrive)}%`,
                        }}
                        title={`waiting ${v.wait_minutes}m for the window to open`}
                      />
                    )}
                    <div
                      className="tl-job"
                      draggable={!busy}
                      onDragStart={() => setDragJob(v.job_id)}
                      onDragEnd={() => {
                        setDragJob(null);
                        setHoverTech(null);
                      }}
                      style={{
                        left: `${pct(start)}%`,
                        width: `${Math.max(pct(end) - pct(start), 0.8)}%`,
                        background: colour,
                      }}
                      title={`${v.job_ref} ${v.customer}\n${v.start}–${v.end}\ndrag onto another technician to reassign`}
                    >
                      <span>{v.job_ref}</span>
                    </div>
                  </div>
                );
              })}
            </div>
          </div>
        );
      })}

      {dragJob !== null && (
        <p className="tl-hint">Drop onto a technician row to reassign.</p>
      )}
    </div>
  );
}
