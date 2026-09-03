import { beforeEach, describe, expect, it } from "vitest";
import { ApiError, OfflineError } from "@/lib/api";
import {
  clearForTests,
  counts,
  enqueue,
  failedItems,
  markFailed,
  markSeen,
  pendingItems,
  queuedAcks,
} from "@/lib/outbox";
import { drain } from "@/lib/sync";

const ack = (changeId: number, jobId = 1) => ({
  kind: "ack" as const,
  jobId,
  payload: { changeId },
});

beforeEach(async () => {
  await clearForTests();
});

describe("acknowledging a change", () => {
  it("queues like every other write", async () => {
    await enqueue(ack(42));
    const [item] = await pendingItems();
    expect(item.kind).toBe("ack");
    if (item.kind !== "ack") throw new Error("unreachable");
    expect(item.payload.changeId).toBe(42);
  });

  it("counts as acknowledged before it has sent", async () => {
    // Without this, tapping "Got it" in a basement would dismiss the
    // interrupt until the next refresh and then have it come straight back.
    await enqueue(ack(42));
    await enqueue(ack(43));
    expect(queuedAcks(await pendingItems())).toEqual(new Set([42, 43]));
  });

  it("is nothing to acknowledge when the queue holds no acks", async () => {
    await enqueue({
      kind: "status",
      jobId: 1,
      payload: { id: "s1", status: "en_route", at: "2026-09-03T08:00:00+08:00" },
    });
    expect(queuedAcks(await pendingItems()).size).toBe(0);
  });

  it("drains in order behind the work it follows", async () => {
    // A technician finishes a job and dismisses the notice about it in the
    // same dead zone. Both go out, oldest first.
    await enqueue({
      kind: "status",
      jobId: 9,
      payload: { id: "s9", status: "complete", at: "2026-09-03T10:15:00+08:00" },
    });
    await enqueue(ack(7, 9));

    const seen: string[] = [];
    await drain(async (i) => {
      seen.push(i.kind);
    });
    expect(seen).toEqual(["status", "ack"]);
    expect(await pendingItems()).toHaveLength(0);
  });

  it("survives a dead zone rather than being lost", async () => {
    await enqueue(ack(42));
    await drain(async () => {
      throw new OfflineError();
    });
    expect(await counts()).toEqual({ pending: 1, failed: 0 });
    expect(queuedAcks(await pendingItems())).toEqual(new Set([42]));
  });

  it("gives up on a change that is no longer there", async () => {
    // 404 on an ack means the change was deleted or was never this
    // technician's. Retrying forever would wedge the queue.
    await enqueue(ack(42));
    const result = await drain(async () => {
      throw new ApiError(404, "no change 42");
    });
    expect(result).toMatchObject({ failed: 1, stopped: null });
  });
});

describe("explaining a failure once", () => {
  it("stops counting a failure the technician has been shown", async () => {
    // The count exists to get their attention. Once the interrupt has said
    // what happened, leaving it on screen forever would mean the warning
    // never clears and stops meaning anything.
    const item = await enqueue({
      kind: "completion",
      jobId: 19,
      payload: {
        id: "c19",
        parts_used: [],
        notes: null,
        at: "2026-09-03T10:15:00+08:00",
        photo_base64: null,
      },
    });
    await markFailed(item.seq, 404, "no job 19");
    expect((await counts()).failed).toBe(1);

    await markSeen(item.seq);
    expect(await counts()).toEqual({ pending: 0, failed: 0 });
  });

  it("keeps the record after it has been seen", async () => {
    // Marked, never deleted. A 404 usually means a job was reassigned away,
    // and the fact that the technician reported doing the work is exactly
    // what somebody may need to reconstruct later.
    const item = await enqueue({
      kind: "status",
      jobId: 19,
      payload: { id: "s19", status: "complete", at: "2026-09-03T10:15:00+08:00" },
    });
    await markFailed(item.seq, 404, "no job 19");
    await markSeen(item.seq);

    expect(await failedItems()).toHaveLength(0);
    expect(await pendingItems()).toHaveLength(0);
    // Still on disk, just no longer shouting.
    const { openDb, promisify, withOutbox } = await import("@/lib/db");
    await openDb();
    const all = await withOutbox("readonly", async (store) =>
      promisify(store.getAll()),
    );
    expect((all as { state: string }[]).map((i) => i.state)).toEqual(["seen"]);
  });

  it("only marks something that actually failed", async () => {
    const item = await enqueue(ack(42));
    await markSeen(item.seq);
    // Still pending -- markSeen must not quietly retire unsent work.
    expect((await counts()).pending).toBe(1);
  });
});
