// The only place that talks to the API.
//
// Every `/field/*` call goes through `fieldFetch`, which attaches the bearer
// token. That is deliberate: phase 6 makes writes go to IndexedDB first and
// the network second, and having one function to change is the difference
// between a swap and a rewrite.

export const API_BASE =
  process.env.NEXT_PUBLIC_API_BASE ?? "http://localhost:8000";

/**
 * An HTTP failure that kept its status code.
 *
 * The status is what callers branch on -- 401 means the token is dead and the
 * technician has to sign in again, everything else means try later -- and a
 * plain `Error` throws that away.
 */
export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

/** Thrown when the request never reached the server at all. */
export class OfflineError extends Error {
  constructor() {
    super("no connection");
    this.name = "OfflineError";
  }
}

async function parseError(res: Response): Promise<string> {
  try {
    const body = await res.json();
    return body.detail ?? res.statusText;
  } catch {
    return res.statusText;
  }
}

/** Unauthenticated call. Only redeem needs this. */
export async function publicFetch<T>(
  path: string,
  init?: RequestInit,
): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`${API_BASE}${path}`, {
      ...init,
      headers: { "content-type": "application/json", ...(init?.headers ?? {}) },
      cache: "no-store",
    });
  } catch {
    // fetch() rejects only on a network-level failure, which here means no
    // signal. Distinguished from an HTTP error because the advice differs:
    // one is "try again when you have bars", the other is "that code is wrong".
    throw new OfflineError();
  }
  if (!res.ok) throw new ApiError(res.status, await parseError(res));
  return res.json() as Promise<T>;
}

/** Authenticated call with no response body. 204s have nothing to parse. */
export async function fieldFetchVoid(
  path: string,
  token: string,
  init?: RequestInit,
): Promise<void> {
  let res: Response;
  try {
    res = await fetch(`${API_BASE}${path}`, {
      ...init,
      headers: {
        "content-type": "application/json",
        Authorization: `Bearer ${token}`,
        ...(init?.headers ?? {}),
      },
      cache: "no-store",
    });
  } catch {
    throw new OfflineError();
  }
  if (!res.ok) throw new ApiError(res.status, await parseError(res));
}

/** Authenticated call. Attaches the stored bearer token. */
export async function fieldFetch<T>(
  path: string,
  token: string,
  init?: RequestInit,
): Promise<T> {
  return publicFetch<T>(path, {
    ...init,
    headers: {
      Authorization: `Bearer ${token}`,
      ...(init?.headers ?? {}),
    },
  });
}

// --- Shapes the API returns -------------------------------------------------

export interface TechnicianOut {
  id: number;
  name: string;
  skills: string[];
  shift_start: string;
  shift_end: string;
  lat: number;
  lon: number;
  van_stock: Record<string, number>;
  max_jobs: number;
}

export interface SessionOut {
  token: string;
  technician: TechnicianOut;
}

export interface TechnicianMe {
  id: number;
  name: string;
  shift_start: string;
  shift_end: string;
}

// --- Calls ------------------------------------------------------------------

export function redeemCode(code: string): Promise<SessionOut> {
  return publicFetch<SessionOut>("/field/auth/redeem", {
    method: "POST",
    body: JSON.stringify({ code }),
  });
}

export function fetchMe(token: string): Promise<TechnicianMe> {
  return fieldFetch<TechnicianMe>("/field/me", token);
}


// --- The technician's day ---------------------------------------------------
//
// Snake_case, matching the API, and used directly in components rather than
// mapped to camelCase first. Same convention as web/lib/api.ts: one file
// knows the wire shapes, and a rename on the server surfaces as a type error
// here instead of silently producing `undefined` two layers down.

export type JobStatus = "upcoming" | "en_route" | "arrived" | "complete";

export interface FieldJob {
  id: number;
  sequence: number;
  customer: string;
  area: string | null;
  address: string | null;
  phone: string | null;
  service_type: string | null;
  fault_description: string | null;
  notes: string | null;

  lat: number;
  lon: number;

  /** ISO 8601 WITH the Malaysian offset -- see hhmm() in lib/format.ts. */
  arrive: string;
  depart: string;
  duration_seconds: number;

  /** The promised window if there is one, the SLA window otherwise. */
  window_start: string;
  window_end: string;
  window_is_promise: boolean;

  parts: string[];
  status: JobStatus;
  /**
   * A completion has been recorded for this job.
   *
   * Not the same as `status === "complete"`. The status comes from the event
   * log and the completion is a separate record, so there is a real window --
   * usually milliseconds, but a whole dead zone when offline -- where a job is
   * done and its paperwork is not yet in.
   */
  completed: boolean;
}

export interface FieldDay {
  day: string;
  technician_id: number;
  technician_name: string;
  /** Null means no successful solve for this day yet -- an empty day. */
  run_id: number | null;
  /** The server's clock. Phase 6 uses it to measure this device's skew. */
  server_time: string;
  finish_estimate: string | null;
  /** Every part code the system knows, so the Complete screen works offline. */
  parts_catalogue: string[];
  jobs: FieldJob[];
}

export function fetchToday(token: string, day?: string): Promise<FieldDay> {
  const query = day ? `?day=${day}` : "";
  return fieldFetch<FieldDay>(`/field/today${query}`, token);
}

// --- Reporting a status (field phase 5) -------------------------------------

export interface StatusEventBody {
  /** Client-generated. The same id replayed is discarded, not recorded twice. */
  id: string;
  status: Exclude<JobStatus, "upcoming">;
  /** ISO 8601 WITH an offset. The API rejects a naive timestamp at the schema. */
  at: string;
  device_seq: number;
}

export interface StatusEventResult {
  id: string;
  job_id: number;
  status: string;
  occurred_at: string;
  recorded_at: string;
  /** The server did not believe this device's clock and clamped the time. */
  time_adjusted: boolean;
  /** This id was already on file; nothing new was written. */
  duplicate: boolean;
  /** The job's furthest-along status after this event. */
  job_status: JobStatus;
}

export function postStatus(
  token: string,
  jobId: number,
  body: StatusEventBody,
): Promise<StatusEventResult> {
  return fieldFetch<StatusEventResult>(`/field/jobs/${jobId}/status`, token, {
    method: "POST",
    body: JSON.stringify(body),
  });
}

// --- Completing a job (field phase 7) ---------------------------------------

export interface CompletionBody {
  id: string;
  parts_used: string[];
  notes: string | null;
  at: string;
  /** Downscaled JPEG as base64, no `data:` prefix. */
  photo_base64: string | null;
}

export interface CompletionResult {
  job_id: number;
  parts_used: string[];
  notes: string | null;
  photo_key: string | null;
  completed_at: string;
  recorded_at: string;
  time_adjusted: boolean;
  /** This job was already completed; nothing new was written. */
  duplicate: boolean;
}

export function postCompletion(
  token: string,
  jobId: number,
  body: CompletionBody,
): Promise<CompletionResult> {
  return fieldFetch<CompletionResult>(`/field/jobs/${jobId}/complete`, token, {
    method: "POST",
    body: JSON.stringify(body),
  });
}

// --- Schedule changes (field phase 8) ---------------------------------------

export type ChangeKind = "assigned" | "removed" | "retimed" | "cancelled";

/**
 * The kinds that TAKE OVER the screen.
 *
 * Straight from the spec's trigger: "when dispatch reassigns or cancels
 * something belonging to this technician". A retime is recorded and shown,
 * but it does not interrupt -- a re-solve moves several jobs by a quarter of
 * an hour most times it runs, and a full-screen takeover on each would train
 * people to dismiss without reading. The one that mattered would go with the
 * rest.
 */
export const INTERRUPTING: ReadonlySet<ChangeKind> = new Set<ChangeKind>([
  "assigned",
  "removed",
  "cancelled",
]);

export interface ScheduleChange {
  id: number;
  job_id: number;
  kind: ChangeKind;
  detail: {
    customer?: string;
    area?: string | null;
    address?: string | null;
    previous_arrive?: string | null;
    new_arrive?: string | null;
    moved_to?: string | null;
    moved_from?: string | null;
  };
  created_at: string;
}

export function fetchChanges(token: string): Promise<ScheduleChange[]> {
  return fieldFetch<ScheduleChange[]>("/field/changes", token);
}

export async function postAck(token: string, changeId: number): Promise<void> {
  await fieldFetchVoid(`/field/changes/${changeId}/ack`, token, { method: "POST" });
}
