// Handing off to the apps that already do this well.
//
// Navigation and calling are solved problems owned by software the technician
// already has installed and already trusts. Waypoint's job is to get them
// there in one tap with the right destination, and then get out of the way.

import type { FieldJob } from "@/lib/api";

/**
 * A `tel:` URI. Null when the job has no number.
 *
 * The stored number is E.164 (`+60312345678`) rather than local format,
 * because a local number with a leading 0 does not dial reliably from a
 * handset that thinks it is somewhere else -- a roaming phone, or one whose
 * SIM region was never set.
 */
export function telUri(job: FieldJob): string | null {
  if (!job.phone) return null;
  // Strip the spaces and dashes a human-entered number picks up. `tel:`
  // tolerates them, but not every dialler does.
  return `tel:${job.phone.replace(/[^\d+]/g, "")}`;
}

/**
 * A navigation URI for this job's coordinates.
 *
 * The spec says "geo URI", and on Android that is exactly right: `geo:` opens
 * the system chooser, so Waze and Google Maps both offer themselves and the
 * technician picks the one they actually use. In Malaysia that matters --
 * Waze is not a fallback here, it is what people drive with.
 *
 * **`geo:` does nothing on iOS.** Safari does not register the scheme, so a
 * bare geo link is a dead button on an iPhone rather than a degraded one.
 * That is a real gap in the spec rather than a detail, so this detects iOS
 * and hands off to Apple Maps instead. It is not a chooser -- iOS will open
 * Apple Maps even when Google Maps is installed -- but a working handoff to
 * the wrong preferred app beats a button that does nothing.
 *
 * Coordinates, not the address string. The address is synthetic-ish and only
 * ever meant to be read by a human; the coordinates are what the solver
 * routed against, so navigating to them takes the technician to the same
 * place the schedule assumed they were going.
 */
export function navigationUri(job: FieldJob, userAgent: string): string {
  const { lat, lon } = job;

  if (isApple(userAgent)) {
    // `daddr` = destination address. Apple Maps accepts a coordinate pair,
    // and `dirflg=d` asks for driving directions rather than a dropped pin.
    return `https://maps.apple.com/?daddr=${lat},${lon}&dirflg=d`;
  }

  // geo:lat,lon?q=lat,lon(Label)
  //
  // The leading `geo:lat,lon` is the map centre and the `q=` is the pin. Both
  // are needed: with only the centre, some apps drop no marker at all and the
  // technician gets a map of roughly the right area with nothing on it.
  const label = encodeURIComponent(job.customer);
  return `geo:${lat},${lon}?q=${lat},${lon}(${label})`;
}

/**
 * iPadOS reports itself as a Mac, and has done since iPadOS 13 -- so the
 * touch check is not paranoia, it is the only thing that distinguishes an
 * iPad from a desktop Safari. A desktop falling through to `geo:` is
 * harmless; an iPad doing so is a dead button on a device someone might
 * genuinely be using in a van.
 */
function isApple(userAgent: string): boolean {
  if (/iPad|iPhone|iPod/.test(userAgent)) return true;
  return (
    userAgent.includes("Macintosh") &&
    typeof navigator !== "undefined" &&
    navigator.maxTouchPoints > 1
  );
}
