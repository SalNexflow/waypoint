import { Suspense } from "react";
import { CompleteDetail } from "@/components/CompleteDetail";

/**
 * Complete -- reached as `/complete?id=4412`.
 *
 * Query parameter rather than a path segment, for the same reason `/job` is:
 * one static document serves every job id, so the service worker precaches it
 * once and a technician can finish a job with the radio off. A `/complete/[id]`
 * route could not be prerendered without knowing every job id at build time.
 */
export default function CompletePage() {
  return (
    <Suspense
      fallback={
        <main className="mx-auto flex min-h-dvh w-full max-w-md items-center justify-center">
          <p className="text-[1.5rem] font-bold text-ink-soft">Waypoint</p>
        </main>
      }
    >
      <CompleteDetail />
    </Suspense>
  );
}
