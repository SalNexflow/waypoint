"use client";

import { useSearchParams } from "next/navigation";
import { CompleteScreen } from "@/components/CompleteScreen";

/** Reads the job id out of the query string. See components/JobDetail.tsx. */
export function CompleteDetail() {
  const params = useSearchParams();
  const raw = params.get("id");
  const id = raw !== null && /^\d+$/.test(raw) ? Number(raw) : null;
  return <CompleteScreen jobId={id} />;
}
