import { Suspense } from "react";
import { JobDetail } from "@/components/JobDetail";

/**
 * Job detail -- reached as `/job?id=4412`.
 *
 * A QUERY PARAMETER rather than a `/job/[id]` path segment, and that is a
 * deliberate trade for an offline-first app.
 *
 * A dynamic path segment cannot be prerendered without knowing every job id
 * at build time, so `/job/4412` would be server-rendered on demand -- and a
 * technician deep-linking to it with no signal would get the service
 * worker's fallback shell instead of the job. `/job` is one static document
 * that the service worker caches once and that serves every job id, because
 * the id arrives in the query string and the data comes from the cached day.
 *
 * The cost is a slightly uglier URL, on a screen where nobody ever sees the
 * URL: the app is installed to a home screen and runs without browser chrome.
 */
export default function JobPage() {
  // useSearchParams() opts a component into client-side rendering, and Next
  // requires a Suspense boundary around it so the rest of the route can still
  // be prerendered. The fallback is the same wordmark the other screens use
  // while they resolve, so there is no visible seam.
  return (
    <Suspense
      fallback={
        <main className="mx-auto flex min-h-dvh w-full max-w-md items-center justify-center">
          <p className="text-[1.5rem] font-bold text-ink-soft">Waypoint</p>
        </main>
      }
    >
      <JobDetail />
    </Suspense>
  );
}
