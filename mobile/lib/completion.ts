// Finishing a job: two queue items, in one order, always.
//
// A completed job is two facts to the server -- the status event that says
// when it finished, and the record of what it needed. Two endpoints, and the
// event has to land first: `POST .../complete` deliberately does not fabricate
// a status event of its own, because that would put two records of the same
// moment in an append-only log.
//
// Enqueuing them together, here, is what makes the order a property of the
// system rather than something each caller remembers. The outbox is FIFO and
// stops at the first retryable failure, so once they are in, in this order,
// they arrive in this order -- whether that is two seconds later or after
// forty minutes in a basement.

import { enqueue } from "@/lib/outbox";
import { syncNow } from "@/lib/sync";
import { newUuid } from "@/lib/uuid";

export interface CompletionInput {
  jobId: number;
  partsUsed: string[];
  notes: string;
  photoBase64: string | null;
  /**
   * When the WORK finished.
   *
   * Stamped when the Complete screen OPENED, not when Done was tapped. The
   * technician finished the job and then filled in a form; the minute or two
   * of typing is not part of the job, and phase 9 re-plans the afternoon off
   * this number.
   */
  finishedAt: string;
}

/** Queue both halves. Returns once they are durable, not once they are sent. */
export async function submitCompletion(input: CompletionInput): Promise<void> {
  await enqueue({
    kind: "status",
    jobId: input.jobId,
    payload: { id: newUuid(), status: "complete", at: input.finishedAt },
  });

  await enqueue({
    kind: "completion",
    jobId: input.jobId,
    payload: {
      id: newUuid(),
      parts_used: input.partsUsed,
      // Empty textarea means no note, not a note that is the empty string.
      notes: input.notes.trim() || null,
      at: input.finishedAt,
      photo_base64: input.photoBase64,
    },
  });

  // Try now; the syncer takes over if there is no signal.
  void syncNow();
}
