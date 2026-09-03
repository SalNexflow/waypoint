// Formatting helpers. Small, but centralised: a time rendered two different
// ways on two screens is the kind of thing nobody notices until a technician
// does.

/**
 * "14:05" from an ISO timestamp.
 *
 * Uses the offset carried in the string rather than the device's timezone,
 * so a phone left on the wrong timezone still shows Malaysian job times.
 * That is not paranoia: the same class of problem is why `occurred_at` needs
 * a server-side sanity check when the offline queue lands in phase 6.
 */
export function hhmm(iso: string): string {
  const m = /T(\d{2}):(\d{2})/.exec(iso);
  return m ? `${m[1]}:${m[2]}` : "--:--";
}

/** "45 min" / "1h 15m". Input is seconds, per the project convention. */
export function duration(seconds: number): string {
  const mins = Math.round(seconds / 60);
  if (mins < 60) return `${mins} min`;
  const h = Math.floor(mins / 60);
  const m = mins % 60;
  return m === 0 ? `${h}h` : `${h}h ${m}m`;
}

/**
 * Part code -> what a technician calls it.
 *
 * An explicit map rather than a title-case rule, because the rule gets the
 * refrigerants wrong: generic capitalisation produced "Gas r32". R-32 and
 * R-410A are product designations, and a technician reading "Gas r32" off a
 * screen has to stop and decide whether it means what they think it means.
 *
 * The eight codes are the whole catalogue (data/seed/catalog.py PARTS), so
 * this is exhaustive rather than a special-case list. The fallback exists for
 * a part added server-side before this map catches up -- it degrades to
 * readable, not to blank.
 */
const PART_LABELS: Record<string, string> = {
  filter_set: "Filter set",
  gas_r32: "Gas R-32",
  gas_r410a: "Gas R-410A",
  contactor: "Contactor",
  thermostat: "Thermostat",
  drain_pump: "Drain pump",
  pcb_board: "PCB board",
  compressor: "Compressor",
};

export function partLabel(code: string): string {
  const known = PART_LABELS[code];
  if (known) return known;
  const words = code.replace(/_/g, " ");
  return words.charAt(0).toUpperCase() + words.slice(1);
}
