"use client";

import { useSearchParams } from "next/navigation";
import { JobScreen } from "@/components/JobScreen";

/**
 * Reads the job id out of the query string and hands it on.
 *
 * Split from JobScreen purely so the Suspense boundary in app/job/page.tsx
 * wraps the smallest possible thing: useSearchParams() is what forces client
 * rendering, and keeping it in a four-line component means the rest of the
 * route still prerenders to a cacheable static document.
 */
export function JobDetail() {
  const params = useSearchParams();
  const raw = params.get("id");
  const id = raw !== null && /^\d+$/.test(raw) ? Number(raw) : null;
  return <JobScreen jobId={id} />;
}
