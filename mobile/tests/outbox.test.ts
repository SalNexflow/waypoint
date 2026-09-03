import { beforeEach, describe, expect, it } from "vitest";
import { drained, resetDbForTests, serial } from "@/lib/db";
import {
  type OutboxItem,
  clearForTests,
  counts,
  enqueue,
  failedItems,
  markFailed,
  noteAttempt,
  pendingItems,
  queuedStatuses,
  remove,
} from "@/lib/outbox";

function status(jobId: number, s: "en_route" | "arrived" | "complete") {
  return {
    kind: "status" as const,
    jobId,
    payload: { id: `id-${jobId}-${s}`, status: s, at: "2026-09-03T08:00:00+08:00" },
  };
}

/** Narrow to a status item. A completion has no `status` to read. */
function statusOf(item: OutboxItem): string {
  if (item.kind !== "status") throw new Error("expected a status item");
  return item.payload.status;
}

beforeEach(async () => {
  await clearForTests();
});

describe("durability and ordering", () => {
  it("keeps items in the order they were enqueued", async () => {
    await enqueue(status(1, "en_route"));
    await enqueue(status(2, "en_route"));
    await enqueue(status(1, "arrived"));

    const items = await pendingItems();
    expect(items.map((i) => [i.jobId, statusOf(i)])).toEqual([
      [1, "en_route"],
      [2, "en_route"],
      [1, "arrived"],
    ]);
  });

  it("assigns a strictly increasing seq", async () => {
    const a = await enqueue(status(1, "en_route"));
    const b = await enqueue(status(1, "arrived"));
    const c = await enqueue(status(1, "complete"));
    expect(a.seq).toBeLessThan(b.seq);
    expect(b.seq).toBeLessThan(c.seq);
  });

  it("survives the connection being dropped and reopened", async () => {
    // The closest a test can get to "app closed, device restarted": forget
    // the cached connection entirely and open the database again from cold.
    await enqueue(status(7, "arrived"));
    resetDbForTests();

    const items = await pendingItems();
    expect(items).toHaveLength(1);
    expect(items[0].jobId).toBe(7);
  });

  it("does not reuse a seq after an item is removed", async () => {
    const a = await enqueue(status(1, "en_route"));
    await remove(a.seq);
    const b = await enqueue(status(1, "arrived"));
    expect(b.seq).toBeGreaterThan(a.seq);
  });
});

describe("the serial write queue", () => {
  it("runs operations one at a time, in call order", async () => {
    const order: string[] = [];
    const op = (name: string, ms: number) => () =>
      new Promise<void>((resolve) =>
        setTimeout(() => {
          order.push(name);
          resolve();
        }, ms),
      );

    // Deliberately slowest-first. Without the mutex these finish in duration
    // order (c, b, a); with it, in call order.
    const all = Promise.all([
      serial(op("a", 30)),
      serial(op("b", 20)),
      serial(op("c", 1)),
    ]);
    await all;
    expect(order).toEqual(["a", "b", "c"]);
  });

  it("is not wedged by an operation that throws", async () => {
    const boom = serial(() => Promise.reject(new Error("disk full")));
    await expect(boom).rejects.toThrow("disk full");

    // The classic bug this guards: a chain built with `.then(op)` alone stays
    // rejected forever, and every later write silently never runs.
    const after = await serial(async () => "still working");
    expect(after).toBe("still working");
  });

  it("serialises concurrent enqueues without losing or colliding any", async () => {
    await Promise.all(
      Array.from({ length: 25 }, (_, i) => enqueue(status(i, "en_route"))),
    );
    await drained();

    const items = await pendingItems();
    expect(items).toHaveLength(25);
    expect(new Set(items.map((i) => i.seq)).size).toBe(25);
  });

  it("keeps an enqueue arriving mid-drain out of the batch being sent", async () => {
    // The read that a drain does and a write that lands during it must not
    // interleave. Both go through the mutex, so the read sees a consistent
    // snapshot and the late arrival lands after it.
    await enqueue(status(1, "en_route"));
    const [snapshot] = await Promise.all([
      pendingItems(),
      enqueue(status(2, "en_route")),
    ]);
    expect(snapshot).toHaveLength(1);
    expect((await pendingItems())).toHaveLength(2);
  });
});

describe("failure states", () => {
  it("counts attempts without losing the item", async () => {
    const item = await enqueue(status(1, "en_route"));
    await noteAttempt(item.seq, "network down");
    await noteAttempt(item.seq, "network down");

    const [again] = await pendingItems();
    expect(again.attempts).toBe(2);
    expect(again.lastError).toBe("network down");
    expect(again.state).toBe("pending");
  });

  it("marks a permanent failure rather than deleting it", async () => {
    // A 404 means the job was reassigned while the phone was offline.
    // Dropping the record would mean the technician's completed job vanished
    // with nothing to show it was ever reported.
    const item = await enqueue(status(1, "complete"));
    await markFailed(item.seq, 404, "no job 1");

    expect(await pendingItems()).toHaveLength(0);
    const failed = await failedItems();
    expect(failed).toHaveLength(1);
    expect(failed[0].failedStatus).toBe(404);
    expect(statusOf(failed[0])).toBe("complete");
  });

  it("reports pending and failed separately", async () => {
    const a = await enqueue(status(1, "en_route"));
    await enqueue(status(2, "en_route"));
    await markFailed(a.seq, 404, "gone");

    expect(await counts()).toEqual({ pending: 1, failed: 1 });
  });
});

describe("merging queued work into the day", () => {
  const item = (jobId: number, s: string, seq: number): OutboxItem => ({
    seq,
    kind: "status",
    jobId,
    payload: { id: `e${seq}`, status: s as never, at: "2026-09-03T08:00:00+08:00" },
    state: "pending",
    attempts: 0,
    createdAt: "2026-09-03T08:00:00+08:00",
  });

  it("takes the furthest-along status per job, not the newest", async () => {
    // Same rank rule the server uses. A queued en_route that has not sent yet
    // must not un-complete a job the technician already finished.
    const map = queuedStatuses([
      item(1, "complete", 1),
      item(1, "en_route", 2),
      item(2, "arrived", 3),
    ]);
    expect(map.get(1)).toBe("complete");
    expect(map.get(2)).toBe("arrived");
  });

  it("ignores jobs with nothing queued", async () => {
    expect(queuedStatuses([]).size).toBe(0);
  });
});
