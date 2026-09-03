import { beforeEach, describe, expect, it } from "vitest";
import { ApiError, OfflineError } from "@/lib/api";
import {
  type OutboxItem,
  clearForTests,
  counts,
  enqueue,
  failedItems,
  pendingItems,
} from "@/lib/outbox";
import { classify, drain } from "@/lib/sync";

function status(jobId: number, s: "en_route" | "arrived" | "complete") {
  return {
    kind: "status" as const,
    jobId,
    payload: { id: `id-${jobId}-${s}`, status: s, at: "2026-09-03T08:00:00+08:00" },
  };
}

function statusOf(item: OutboxItem): string {
  if (item.kind !== "status") throw new Error("expected a status item");
  return item.payload.status;
}

beforeEach(async () => {
  await clearForTests();
});

describe("classifying a failure", () => {
  it("treats no reply at all as retryable", () => {
    // fetch() rejects only when the request never reached a server -- which
    // on a phone means a dead zone, not a decision by anyone.
    expect(classify(new OfflineError())).toBe("retry");
    expect(classify(new TypeError("Failed to fetch"))).toBe("retry");
  });

  it("retries server-side failures", () => {
    expect(classify(new ApiError(500, "boom"))).toBe("retry");
    expect(classify(new ApiError(503, "unavailable"))).toBe("retry");
  });

  it("retries the two 4xx that mean 'later', not 'never'", () => {
    expect(classify(new ApiError(408, "timeout"))).toBe("retry");
    expect(classify(new ApiError(429, "slow down"))).toBe("retry");
  });

  it("gives up on a request that can never be accepted", () => {
    // 404 is the one that happens in practice: the job was reassigned to
    // someone else while this phone was offline. Retrying it forever would
    // wedge every item behind it.
    expect(classify(new ApiError(404, "no job 19"))).toBe("permanent");
    expect(classify(new ApiError(422, "bad status"))).toBe("permanent");
    expect(classify(new ApiError(400, "malformed"))).toBe("permanent");
  });

  it("treats a dead token as neither the item's fault nor permanent", () => {
    // The item is fine and will send once the technician signs in again.
    expect(classify(new ApiError(401, "invalid token"))).toBe("auth");
  });
});

describe("draining", () => {
  it("sends everything in order and empties the queue", async () => {
    await enqueue(status(1, "en_route"));
    await enqueue(status(2, "en_route"));
    await enqueue(status(1, "arrived"));

    const seen: Array<[number, string]> = [];
    const result = await drain(async (i) => {
      seen.push([i.jobId, statusOf(i)]);
    });

    expect(seen).toEqual([
      [1, "en_route"],
      [2, "en_route"],
      [1, "arrived"],
    ]);
    expect(result).toMatchObject({ sent: 3, failed: 0, stopped: null, remaining: 0 });
    expect(await pendingItems()).toHaveLength(0);
  });

  it("stops at the first retryable failure and keeps everything after it", async () => {
    // Order is the point. Pressing on past a failure would deliver a later
    // item before an earlier one the moment the earlier one's send failed --
    // and phase 7's completion has to land after its own status event.
    await enqueue(status(1, "en_route"));
    await enqueue(status(2, "en_route"));
    await enqueue(status(3, "en_route"));

    const seen: number[] = [];
    const result = await drain(async (i) => {
      seen.push(i.jobId);
      if (i.jobId === 2) throw new OfflineError();
    });

    expect(seen).toEqual([1, 2]);
    expect(result).toMatchObject({ sent: 1, stopped: "offline" });
    const left = await pendingItems();
    expect(left.map((i) => i.jobId)).toEqual([2, 3]);
    expect(left[0].attempts).toBe(1);
  });

  it("steps over a permanent failure so it cannot hold up the rest", async () => {
    await enqueue(status(1, "en_route"));
    await enqueue(status(2, "complete")); // reassigned away: 404 forever
    await enqueue(status(3, "en_route"));

    const result = await drain(async (i) => {
      if (i.jobId === 2) throw new ApiError(404, "no job 2");
    });

    expect(result).toMatchObject({ sent: 2, failed: 1, stopped: null, remaining: 0 });
    expect(await pendingItems()).toHaveLength(0);

    const dead = await failedItems();
    expect(dead).toHaveLength(1);
    expect(dead[0].jobId).toBe(2);
    expect(dead[0].failedStatus).toBe(404);
  });

  it("stops on a dead token and leaves the item exactly as it was", async () => {
    await enqueue(status(1, "en_route"));
    await enqueue(status(2, "en_route"));

    const result = await drain(async () => {
      throw new ApiError(401, "invalid or revoked token");
    });

    expect(result.stopped).toBe("auth");
    expect(result.sent).toBe(0);
    // Still pending, not failed -- these go out after signing back in.
    expect(await counts()).toEqual({ pending: 2, failed: 0 });
  });

  it("deletes an item the server says it already had", async () => {
    // `duplicate: true` means an earlier attempt got through and the reply
    // was lost. That is a success, and the whole reason the client mints the
    // event id. Treating it as a failure would retry forever.
    await enqueue(status(1, "en_route"));
    const result = await drain(async () => {
      /* server responded 201 duplicate=true */
    });
    expect(result.sent).toBe(1);
    expect(await pendingItems()).toHaveLength(0);
  });

  it("never sends an item twice across a failure and a retry", async () => {
    // The definition-of-done clause: "reconnecting drains the queue with no
    // duplicates, verified by event count".
    await enqueue(status(1, "en_route"));
    await enqueue(status(2, "arrived"));

    const sent: string[] = [];
    let offline = true;
    const send = async (i: OutboxItem) => {
      if (offline) throw new OfflineError();
      if (i.kind === "ack") throw new Error("no acks in this test");
      sent.push(i.payload.id);
    };

    await drain(send); // dead zone: nothing gets through
    await drain(send); // still nothing
    expect(sent).toEqual([]);

    offline = false;
    await drain(send); // signal returns
    await drain(send); // and a redundant kick from `visibilitychange`

    expect(sent).toEqual(["id-1-en_route", "id-2-arrived"]);
    expect(new Set(sent).size).toBe(sent.length);
    expect(await pendingItems()).toHaveLength(0);
  });

  it("resumes from where it stopped rather than starting over", async () => {
    await enqueue(status(1, "en_route"));
    await enqueue(status(2, "en_route"));
    await enqueue(status(3, "en_route"));

    const sent: number[] = [];
    let allow = 1;
    const send = async (i: OutboxItem) => {
      if (sent.length >= allow) throw new OfflineError();
      sent.push(i.jobId);
    };

    await drain(send);
    expect(sent).toEqual([1]);

    allow = 3;
    await drain(send);
    expect(sent).toEqual([1, 2, 3]);
    expect(await pendingItems()).toHaveLength(0);
  });

  it("carries the queue position as the device sequence", async () => {
    // One transactional counter for both, so a gap can only ever mean an item
    // was sent -- never that two items shared an order.
    const a = await enqueue(status(1, "en_route"));
    const b = await enqueue(status(2, "en_route"));

    const seqs: number[] = [];
    await drain(async (i) => {
      seqs.push(i.seq);
    });
    expect(seqs).toEqual([a.seq, b.seq]);
    expect(seqs[0]).toBeLessThan(seqs[1]);
  });

  it("does nothing, successfully, on an empty queue", async () => {
    const result = await drain(async () => {
      throw new Error("should not be called");
    });
    expect(result).toEqual({ sent: 0, failed: 0, stopped: null, remaining: 0 });
  });
});
