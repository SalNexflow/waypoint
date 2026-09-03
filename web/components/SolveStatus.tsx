"use client";

import { SolveMetrics, minutesToHhmm } from "@/lib/api";

interface Props {
  metrics: SolveMetrics;
  previous?: SolveMetrics | null;
  runId: number | null;
}

/**
 * Objective value, progress, and comparison to the previous run.
 *
 * Surfaces two things the spec insists on and that are easy to bury: whether
 * the solver PROVED optimality or merely ran out of time, and whether the
 * travel matrix was real. A number from the haversine fallback is provisional
 * and says so here rather than looking like a result.
 */
export default function SolveStatus({ metrics, previous, runId }: Props) {
  const delta = previous
    ? {
        travel: metrics.travel_minutes - previous.travel_minutes,
        assigned: metrics.assigned - previous.assigned,
      }
    : null;

  return (
    <div className="status">
      <div className="stat">
        <span className="k">assigned</span>
        <span className="v">
          {metrics.assigned}/{metrics.total_jobs}
          {delta && delta.assigned !== 0 && (
            <em className={delta.assigned > 0 ? "up" : "down"}>
              {delta.assigned > 0 ? "+" : ""}
              {delta.assigned}
            </em>
          )}
        </span>
      </div>

      <div className="stat">
        <span className="k">driving</span>
        <span className="v">
          {minutesToHhmm(metrics.travel_minutes)}
          {delta && delta.travel !== 0 && (
            <em className={delta.travel < 0 ? "up" : "down"}>
              {delta.travel > 0 ? "+" : ""}
              {delta.travel}m
            </em>
          )}
        </span>
      </div>

      <div className="stat">
        <span className="k">solve time</span>
        <span className="v">{(metrics.solver_wall_ms / 1000).toFixed(1)}s</span>
      </div>

      <div className="stat">
        <span className="k">optimality</span>
        <span className="v">
          {metrics.fell_back ? (
            <span
              className="bad"
              title={
                "The time limit expired before the solver found any solution. " +
                "This is the greedy fallback plan: it is valid and workable, " +
                "but nothing was optimised. Raise the time limit and re-solve."
              }
            >
              not solved
            </span>
          ) : metrics.proved_optimal ? (
            <span className="ok">proved</span>
          ) : (
            <span className="warn" title="hit the time limit with the best solution found so far">
              best found
            </span>
          )}
        </span>
      </div>

      <div className="stat">
        <span className="k">checker</span>
        <span className="v">
          {metrics.valid ? (
            <span className="ok">valid</span>
          ) : (
            <span className="bad" title={metrics.violations.join("\n")}>
              {metrics.violations.length} violation(s)
            </span>
          )}
        </span>
      </div>

      <div className="stat">
        <span className="k">travel data</span>
        <span className="v">
          {metrics.reportable ? (
            <span className="ok">OSRM</span>
          ) : (
            <span className="bad" title="haversine fallback: optimistic by roughly a third, not a reportable figure">
              {metrics.matrix_source} (provisional)
            </span>
          )}
        </span>
      </div>

      {runId !== null && (
        <div className="stat">
          <span className="k">run</span>
          <span className="v">#{runId}</span>
        </div>
      )}
    </div>
  );
}
