import { beforeEach, describe, expect, it } from "vitest";
import { ApiError, OfflineError } from "@/lib/api";
import { type OutboxItem, clearForTests, enqueue, pendingItems } from "@/lib/outbox";
import { drain } from "@/lib/sync";

function statusItem(jobId: number, s: "en_route" | "arrived" | "complete") {
  return {
    kind: "status" as const,
    jobId,
    payload: { id: `s-${jobId}-${s}`, status: s, at: "2026-09-03T10:15:00+08:00" },
  };
}

function completionItem(jobId: number, photo: string | null = null) {
  return {
    kind: "completion" as const,
    jobId,
    payload: {
      id: `c-${jobId}`,
      parts_used: ["gas_r32"],
      notes: "Regassed.",
      at: "2026-09-03T10:15:00+08:00",
      photo_base64: photo,
    },
  };
}

beforeEach(async () => {
  await clearForTests();
});

describe("finishing a job", () => {
  it("queues the status event before the completion", async () => {
    // The order matters and is not incidental. POST .../complete deliberately
    // does not fabricate a status event -- that would put two records of the
    // same moment in an append-only log -- so the event has to arrive first
    // for the job to read as done.
    await enqueue(statusItem(19, "complete"));
    await enqueue(completionItem(19));

    const kinds = (await pendingItems()).map((i) => i.kind);
    expect(kinds).toEqual(["status", "completion"]);
  });

  it("delivers them in that order even across a dead zone", async () => {
    await enqueue(statusItem(19, "complete"));
    await enqueue(completionItem(19));

    const delivered: string[] = [];
    let allow = 0;
    const send = async (i: OutboxItem) => {
      if (delivered.length >= allow) throw new OfflineError();
      delivered.push(i.kind);
    };

    await drain(send);            // no signal
    expect(delivered).toEqual([]);

    allow = 1;
    await drain(send);            // one gets through, then the radio drops
    expect(delivered).toEqual(["status"]);

    allow = 9;
    await drain(send);            // signal returns
    expect(delivered).toEqual(["status", "completion"]);
    expect(await pendingItems()).toHaveLength(0);
  });

  it("keeps the completion queued when its status event cannot send", async () => {
    // Stopping at the first retryable failure is what prevents the completion
    // overtaking the event it belongs to.
    await enqueue(statusItem(19, "complete"));
    await enqueue(completionItem(19));

    const result = await drain(async () => {
      throw new OfflineError();
    });

    expect(result).toMatchObject({ sent: 0, stopped: "offline" });
    expect((await pendingItems()).map((i) => i.kind)).toEqual([
      "status",
      "completion",
    ]);
  });

  it("carries a photo through the queue unchanged", async () => {
    // Base64 in a plain-JSON payload, so the queue built in phase 6 needed no
    // changes to hold photos -- and a photo survives an app restart exactly
    // the way a status change does.
    const jpeg = "/9j/4AAQSkZJRg==";
    await enqueue(completionItem(19, jpeg));

    const [item] = await pendingItems();
    expect(item.kind).toBe("completion");
    if (item.kind !== "completion") throw new Error("unreachable");
    expect(item.payload.photo_base64).toBe(jpeg);
    expect(item.payload.parts_used).toEqual(["gas_r32"]);
  });

  it("marks a completion for a reassigned job as permanently failed", async () => {
    // The job moved to someone else while the phone was offline. Retrying the
    // 404 forever would wedge everything behind it; deleting it would erase
    // the fact that the technician reported finishing the work.
    await enqueue(completionItem(19));
    await enqueue(statusItem(20, "en_route"));

    const result = await drain(async (i) => {
      if (i.kind === "completion") throw new ApiError(404, "no job 19");
    });

    expect(result).toMatchObject({ sent: 1, failed: 1, stopped: null });
    expect(await pendingItems()).toHaveLength(0);
  });
});
