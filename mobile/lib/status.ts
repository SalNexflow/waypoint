// The three-state action button: what it says, and what it does next.
//
// The transition table lives here rather than inside the component so phase 5
// has one place to wire the endpoint into and phase 6 has one place to make
// write-local-first. Editing the screen to add offline support would mean the
// screen knew about queues, which is exactly the coupling this avoids.

import type { JobStatus } from "@/lib/api";

export interface Transition {
  /** The status the job moves to. */
  to: Exclude<JobStatus, "upcoming">;
  /** What the button says. Written as the technician's own action. */
  label: string;
}

/**
 * What happens when the big button is pressed, given where the job is now.
 *
 * `null` means there is nothing left to do -- the job is finished, and the
 * screen shows a completed state instead of a button. Deliberately a total
 * function over JobStatus: adding a status later is a type error here rather
 * than a button that silently stops appearing.
 */
export const NEXT: Record<JobStatus, Transition | null> = {
  upcoming: { to: "en_route", label: "On my way" },
  en_route: { to: "arrived", label: "Arrived" },
  arrived: { to: "complete", label: "Complete" },
  complete: null,
};

/** Past-tense summary of where a job has got to, for the detail header. */
export const STATUS_LABEL: Record<JobStatus, string> = {
  upcoming: "Not started",
  en_route: "On my way",
  arrived: "On site",
  complete: "Done",
};
