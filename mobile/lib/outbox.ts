// The outbox: what the technician did, waiting for the server to agree.
//
// Everything the phone reports goes in here FIRST and is sent second. That
// ordering is the whole design: a status change is durable the instant it is
// tapped, so a dead zone, a locked phone, a force-quit or a flat battery
// costs nothing. The network is a detail that happens later.
//
// One queue, not one per kind. Phase 7 adds job completions, and a completion
// must land after the `complete` status event it belongs to -- two queues
// would need ordering between them, which is a harder problem than ordering
// within one.

import { OUTBOX, promisify, serial, withOutbox } from "@/lib/db";

export type OutboxKind = "status" | "completion" | "ack";

/**
 * `seen` is for a failed item the technician has been shown and dismissed.
 *
 * Not deleted -- the record that they reported something is the evidence, and
 * a 404 usually means a job was reassigned away, which is exactly the case
 * somebody may need to reconstruct later. Dropping out of the counts is what
 * lets the warning strip clear; without this state it would say "1 update
 * couldn't be saved" until the app was reinstalled.
 */
export type OutboxState = "pending" | "failed" | "seen";

export interface StatusPayload {
  /** Client-generated UUID. The server's idempotency key. */
  id: string;
  status: "en_route" | "arrived" | "complete";
  /** ISO 8601 with offset -- when the technician tapped, not when it sends. */
  at: string;
}

export interface AckPayload {
  changeId: number;
}

export interface CompletionPayload {
  id: string;
  parts_used: string[];
  notes: string | null;
  /** When the WORK finished -- stamped on opening the form, not on submitting it. */
  at: string;
  /**
   * A downscaled JPEG, base64, no `data:` prefix.
   *
   * A string rather than a Blob, which IndexedDB would store more efficiently.
   * Keeping every payload plain JSON means the queue, the drain and their
   * tests are unchanged by photos arriving -- and the client downscales to
   * roughly 300KB first, so the base64 overhead is about 100KB. That trade
   * would go the other way at full resolution.
   */
  photo_base64: string | null;
}

interface ItemFields {
  /** Auto-increment. Queue order AND the `device_seq` sent to the server. */
  seq: number;
  jobId: number;
  state: OutboxState;
  attempts: number;
  createdAt: string;
  lastError?: string;
  /** HTTP status of the failure that made this permanent, when it is. */
  failedStatus?: number;
}

/**
 * Discriminated on `kind`, so reading `payload.status` off a completion is a
 * type error rather than an `undefined` that surfaces as a malformed request
 * three layers away.
 */
export type OutboxItem =
  | (ItemFields & { kind: "status"; payload: StatusPayload })
  | (ItemFields & { kind: "completion"; payload: CompletionPayload })
  | (ItemFields & { kind: "ack"; payload: AckPayload });

export type NewItem =
  | { kind: "status"; jobId: number; payload: StatusPayload }
  | { kind: "completion"; jobId: number; payload: CompletionPayload }
  | { kind: "ack"; jobId: number; payload: AckPayload };

/**
 * Append an item. Returns the stored record, including its assigned `seq`.
 *
 * Serialised like every other write, so an enqueue landing during a drain
 * cannot interleave with it.
 */
export function enqueue(item: NewItem): Promise<OutboxItem> {
  return serial(() =>
    withOutbox("readwrite", async (store) => {
      const record = {
        ...item,
        state: "pending" as const,
        attempts: 0,
        createdAt: new Date().toISOString(),
      };
      const seq = (await promisify(store.add(record))) as number;
      return { ...record, seq } as OutboxItem;
    }),
  );
}

/** Everything still waiting to send, oldest first. */
export function pendingItems(): Promise<OutboxItem[]> {
  return serial(() =>
    withOutbox("readonly", async (store) => {
      const all = (await promisify(store.getAll())) as OutboxItem[];
      // Sorted by `seq` rather than relying on getAll order. getAll DOES
      // return key order, but the guarantee this queue depends on is worth
      // stating in code rather than inheriting from a spec footnote.
      return all
        .filter((i) => i.state === "pending")
        .sort((a, b) => a.seq - b.seq);
    }),
  );
}

/** Items that can never succeed and have stopped being retried. */
export function failedItems(): Promise<OutboxItem[]> {
  return serial(() =>
    withOutbox("readonly", async (store) => {
      const all = (await promisify(store.getAll())) as OutboxItem[];
      return all.filter((i) => i.state === "failed").sort((a, b) => a.seq - b.seq);
    }),
  );
}

export interface OutboxCounts {
  pending: number;
  failed: number;
}

export function counts(): Promise<OutboxCounts> {
  return serial(() =>
    withOutbox("readonly", async (store) => {
      const all = (await promisify(store.getAll())) as OutboxItem[];
      return {
        pending: all.filter((i) => i.state === "pending").length,
        failed: all.filter((i) => i.state === "failed").length,
      };
    }),
  );
}

/**
 * The server accepted it (or already had it). Remove it.
 *
 * Only ever called after a response, never optimistically. An item deleted
 * before confirmation is work destroyed, and there is no way to get it back
 * -- which is why a transient failure leaves the item exactly where it is.
 */
export function remove(seq: number): Promise<void> {
  return serial(() =>
    withOutbox("readwrite", async (store) => {
      await promisify(store.delete(seq));
    }),
  );
}

/** A transient failure: keep the item, record what happened. */
export function noteAttempt(seq: number, error: string): Promise<void> {
  return serial(() =>
    withOutbox("readwrite", async (store) => {
      const item = (await promisify(store.get(seq))) as OutboxItem | undefined;
      if (!item) return;
      await promisify(
        store.put({ ...item, attempts: item.attempts + 1, lastError: error }),
      );
    }),
  );
}

/**
 * A permanent failure: this can never succeed, so stop retrying it.
 *
 * Marked, NOT deleted. The commonest cause is a 404 -- the job was reassigned
 * to someone else while the phone was offline -- and quietly dropping the
 * record would mean a technician's completed job vanished with no trace that
 * it was ever reported. The item stays as evidence, is skipped when draining
 * so it cannot wedge the queue, and is surfaced as a count. Phase 8 is where
 * it becomes a sentence the technician can act on.
 */
export function markFailed(
  seq: number,
  httpStatus: number,
  error: string,
): Promise<void> {
  return serial(() =>
    withOutbox("readwrite", async (store) => {
      const item = (await promisify(store.get(seq))) as OutboxItem | undefined;
      if (!item) return;
      await promisify(
        store.put({
          ...item,
          state: "failed" as const,
          attempts: item.attempts + 1,
          failedStatus: httpStatus,
          lastError: error,
        }),
      );
    }),
  );
}

/**
 * The furthest-along status queued for each job, by the SAME rank order the
 * server uses.
 *
 * This is what makes a tap visible immediately and stay visible across a
 * reload, a screen change and an app restart: the cached day carries what the
 * server last said, and this carries what the technician has done since.
 * Merging them is `max(server, queued)` by rank -- so a queued `en_route`
 * that has not sent yet cannot un-complete a job the server already knows is
 * finished.
 */
const RANK: Record<string, number> = { en_route: 1, arrived: 2, complete: 3 };

export function queuedStatuses(items: OutboxItem[]): Map<number, string> {
  const out = new Map<number, string>();
  for (const item of items) {
    if (item.kind !== "status") continue;
    const current = out.get(item.jobId);
    if (!current || RANK[item.payload.status] > RANK[current]) {
      out.set(item.jobId, item.payload.status);
    }
  }
  return out;
}

/**
 * Change ids acknowledged locally but not yet confirmed by the server.
 *
 * Merged over what `/field/changes` returned, so tapping "Got it" in a
 * basement dismisses the interrupt for good rather than having it reappear on
 * the next refresh.
 */
export function queuedAcks(items: OutboxItem[]): Set<number> {
  const out = new Set<number>();
  for (const item of items) {
    if (item.kind === "ack") out.add(item.payload.changeId);
  }
  return out;
}

/** A failed item has been shown to the technician. Keep it, stop counting it. */
export function markSeen(seq: number): Promise<void> {
  return serial(() =>
    withOutbox("readwrite", async (store) => {
      const item = (await promisify(store.get(seq))) as OutboxItem | undefined;
      if (!item || item.state !== "failed") return;
      await promisify(store.put({ ...item, state: "seen" as const }));
    }),
  );
}

/** Test seam: empty the store. */
export function clearForTests(): Promise<void> {
  return serial(() =>
    withOutbox("readwrite", async (store) => {
      await promisify(store.clear());
    }),
  );
}

export { OUTBOX };
