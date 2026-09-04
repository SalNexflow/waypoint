// Typed client for the Waypoint API. One place that knows the shapes.

export const API_BASE =
  process.env.NEXT_PUBLIC_API_BASE ?? "http://localhost:8000";

export interface Visit {
  job_id: number;
  job_ref: string;
  customer: string;
  technician_id: number;
  technician_ref: string;
  technician_name: string;
  sequence: number;
  arrive: string;
  start: string;
  end: string;
  wait_minutes: number;
  lat: number;
  lon: number;
}

export interface Route {
  technician_id: number;
  technician_ref: string;
  technician_name: string;
  shift_start: string;
  shift_end: string;
  home_lat: number;
  home_lon: number;
  visits: Visit[];
  travel_minutes: number;
  work_minutes: number;
  wait_minutes: number;
}

export interface Unassigned {
  job_id: number;
  job_ref: string;
  customer: string;
  lat: number;
  lon: number;
  reason: string;
  message: string;
}

export interface SolveMetrics {
  status: string;
  proved_optimal: boolean;
  fell_back: boolean;
  objective_value: number | null;
  travel_minutes: number;
  assigned: number;
  total_jobs: number;
  unassigned_count: number;
  solver_wall_ms: number;
  matrix_source: string;
  reportable: boolean;
  valid: boolean;
  violations: string[];
}

export interface SolveResult {
  run_id: number | null;
  day: string;
  metrics: SolveMetrics;
  routes: Route[];
  unassigned: Unassigned[];
}

export interface SolveRun {
  id: number;
  day: string;
  status: string;
  objective_value: number | null;
  travel_seconds_total: number | null;
  unassigned_count: number | null;
  solver_wall_ms: number | null;
  proved_optimal: boolean | null;
  error: string | null;
  created_at: string;
}

export interface ReassignPreview {
  ok: boolean;
  reason: string | null;
  travel_delta_minutes: number;
  unassigned_delta: number;
  moved_jobs: string[];
  customer_calls: number;
  calls: string[];
  valid: boolean;
  result: SolveResult | null;
}

export interface DispatchChange {
  kind: string;
  technician_ref?: string | null;
  technician_name?: string | null;
  job_ref?: string | null;
  minutes?: number | null;
  new_shift_end?: string | null;
  priority?: number | null;
  customer?: string | null;
  confidence: number;
  note?: string | null;
}

export interface DispatchParseResponse {
  understood: boolean;
  change: DispatchChange | null;
  error: string | null;
  raw: string | null;
  provider: string;
}

export interface DispatchApplyResponse {
  ok: boolean;
  reason: string | null;
  summary: string;
  travel_delta_minutes: number;
  unassigned_delta: number;
  customer_calls: number;
  moves: string[];
  valid: boolean;
  result: SolveResult | null;
}

export interface AccessStatus {
  technician_id: number;
  technician_name: string;
  has_live_code: boolean;
  code_expires_at: string | null;
  active_devices: number;
}

export interface IssuedCode {
  technician_id: number;
  technician_name: string;
  code: string;
  expires_at: string;
}

export interface RevokedAccess {
  technician_id: number;
  codes_revoked: number;
  tokens_revoked: number;
}

// --- Dispatcher token (field phase 10) ---
//
// The console had no auth, which was fine on localhost and stopped being fine
// the moment a LAN address went into CORS_ORIGINS. One shared secret, held in
// localStorage, sent on every request.
//
// Not per-dispatcher, and not pretending to be: there is one dispatch team
// sharing one console, and this answers "is this the office" rather than "who
// is this". The technician side is per-person because it decides WHOSE data;
// this decides WHETHER.

const TOKEN_KEY = "waypoint.dispatch.token";

export function getDispatchToken(): string {
  if (typeof window === "undefined") return "";
  try {
    return window.localStorage.getItem(TOKEN_KEY) ?? "";
  } catch {
    return "";
  }
}

export function setDispatchToken(token: string): void {
  try {
    window.localStorage.setItem(TOKEN_KEY, token);
  } catch {
    /* private window; the console works for this session only */
  }
}

export function clearDispatchToken(): void {
  try {
    window.localStorage.removeItem(TOKEN_KEY);
  } catch {
    /* ignore */
  }
}

/** Thrown when the API wants a dispatcher token it did not get. */
export class LockedError extends Error {
  constructor(readonly status: number, message: string) {
    super(message);
    this.name = "LockedError";
  }
}

/**
 * Thrown when the API could not be reached at all.
 *
 * `fetch` rejects with a bare TypeError for DNS failure, a refused connection,
 * a CORS rejection and a dropped network alike, and a TypeError surfaced to a
 * user says nothing. Naming the case here is what lets the console tell
 * "the server is not answering" apart from "this day has no schedule" -- two
 * situations with completely different next steps that otherwise arrive at the
 * catch block indistinguishable.
 */
export class UnreachableError extends Error {
  constructor(readonly cause_: unknown) {
    super("Could not reach the API.");
    this.name = "UnreachableError";
  }
}

/** Thrown when the travel matrix cannot cover the locations asked about. */
export class UnroutableError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "UnroutableError";
  }
}

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const token = getDispatchToken();
  let res: Response;
  try {
    res = await fetch(`${API_BASE}${path}`, {
      ...init,
      headers: {
        "content-type": "application/json",
        // Sent even when empty is pointless, so it is omitted -- and an API with
        // no token configured ignores it either way.
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
        ...(init?.headers ?? {}),
      },
      cache: "no-store",
    });
  } catch (err) {
    throw new UnreachableError(err);
  }
  if (!res.ok) {
    let detail = res.statusText;
    let errorCode: string | undefined;
    try {
      const body = await res.json();
      detail = body.detail ?? JSON.stringify(body);
      errorCode = body.error;
    } catch {
      /* keep statusText */
    }
    // A frozen-matrix deployment answers 503 for a day it has no road times
    // for, which is emphatically NOT "the console is locked" -- checked first
    // because it shares a status code with the case below and would otherwise
    // send someone to an unlock screen over a routing problem.
    if (res.status === 503 && errorCode === "unroutable") {
      throw new UnroutableError(detail);
    }
    // 401 means the token is missing or wrong; 503 means the API is exposed
    // beyond localhost with none configured at all. Both are "you cannot use
    // this console until something is set", which is one screen, not two.
    if (res.status === 401 || res.status === 503) {
      throw new LockedError(res.status, detail);
    }
    throw new Error(`${res.status}: ${detail}`);
  }
  return res.json() as Promise<T>;
}

export const api = {
  health: () => req<{ status: string }>("/health"),

  routing: () =>
    req<{ status: string; source: string; reportable: boolean }>(
      "/health/routing",
    ),

  latest: (day: string) => req<SolveResult>(`/solve/day/${day}/latest`),

  runs: (day?: string) =>
    req<SolveRun[]>(`/solve/runs${day ? `?day=${day}` : ""}`),

  run: (id: number) => req<SolveRun>(`/solve/runs/${id}`),

  result: (id: number) => req<SolveResult>(`/solve/runs/${id}/result`),

  solve: (day: string, timeLimit = 30, workers = 8) =>
    req<SolveRun>("/solve", {
      method: "POST",
      body: JSON.stringify({
        day,
        time_limit_s: timeLimit,
        workers,
      }),
    }),

  reassign: (
    runId: number,
    jobId: number,
    technicianId: number,
    commit = false,
  ) =>
    req<ReassignPreview>("/solve/reassign", {
      method: "POST",
      body: JSON.stringify({
        run_id: runId,
        job_id: jobId,
        technician_id: technicianId,
        commit,
        time_limit_s: 15,
      }),
    }),

  dispatchProvider: () =>
    req<{ provider: string; model: string; ready: boolean; reason: string | null }>(
      "/dispatch/provider",
    ),

  dispatchParse: (text: string, day: string, runId?: number) =>
    req<DispatchParseResponse>("/dispatch/parse", {
      method: "POST",
      body: JSON.stringify({ text, day, run_id: runId ?? null }),
    }),

  dispatchApply: (
    runId: number,
    change: DispatchChange,
    now: string,
    commit: boolean,
  ) =>
    req<DispatchApplyResponse>("/dispatch/apply", {
      method: "POST",
      body: JSON.stringify({
        run_id: runId,
        change,
        now,
        commit,
        time_limit_s: 20,
      }),
    }),

  // --- Technician access (field phase 2) ---
  // These are UNAUTHENTICATED, like the rest of the console. Fine on
  // localhost, not fine anywhere else -- see the note in api/routes/
  // technicians.py.

  accessStatus: () => req<AccessStatus[]>("/technicians/access"),

  issueAccessCode: (technicianId: number) =>
    req<IssuedCode>(`/technicians/${technicianId}/access-code`, {
      method: "POST",
    }),

  revokeAccess: (technicianId: number) =>
    req<RevokedAccess>(`/technicians/${technicianId}/access`, {
      method: "DELETE",
    }),
};

// Colour per technician. Chosen to stay distinguishable on a grey basemap and
// to survive the common forms of colour blindness reasonably well.
export const ROUTE_COLOURS = [
  "#2563eb",
  "#dc2626",
  "#16a34a",
  "#ea580c",
  "#9333ea",
  "#0891b2",
  "#ca8a04",
  "#db2777",
  "#4f46e5",
  "#059669",
  "#b91c1c",
  "#7c3aed",
  "#0d9488",
  "#c2410c",
  "#1d4ed8",
];

export function colourFor(index: number): string {
  return ROUTE_COLOURS[index % ROUTE_COLOURS.length];
}

export function minutesToHhmm(mins: number): string {
  const h = Math.floor(mins / 60);
  const m = mins % 60;
  return h > 0 ? `${h}h${String(m).padStart(2, "0")}m` : `${m}m`;
}

export function hhmmToMinutes(s: string): number {
  const [h, m] = s.split(":").map(Number);
  return h * 60 + m;
}
