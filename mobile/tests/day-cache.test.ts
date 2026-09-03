import { beforeEach, describe, expect, it } from "vitest";
import type { FieldDay } from "@/lib/api";
import { clearDay, loadDay, localDay, saveDay } from "@/lib/day-cache";

const day = (date: string): FieldDay => ({
  day: date,
  technician_id: 1,
  technician_name: "Ahmad Faizal",
  run_id: 4,
  server_time: `${date}T08:00:00+08:00`,
  finish_estimate: `${date}T16:40:00+08:00`,
  parts_catalogue: ["filter_set", "gas_r32"],
  jobs: [
    {
      id: 19,
      sequence: 0,
      customer: "Menara Tower",
      area: "Sentul",
      address: "11, Jalan Setiabakti, 51100 Sentul",
      phone: "+60312735359",
      service_type: "Gas top-up",
      fault_description: "Ice forming on the pipe outside.",
      notes: null,
      lat: 3.1891,
      lon: 101.6862,
      arrive: `${date}T08:03:00+08:00`,
      depart: `${date}T08:43:00+08:00`,
      duration_seconds: 2400,
      window_start: `${date}T09:30:00+08:00`,
      window_end: `${date}T11:30:00+08:00`,
      window_is_promise: true,
      parts: ["gas_r32"],
      status: "upcoming",
      completed: false,
    },
  ],
});

beforeEach(() => {
  clearDay();
});

describe("the cached day", () => {
  it("comes back with everything the screens render", () => {
    saveDay(day("2026-09-03"));
    const cached = loadDay("2026-09-03", 1);

    expect(cached).not.toBeNull();
    expect(cached!.day.jobs[0].address).toBe("11, Jalan Setiabakti, 51100 Sentul");
    expect(cached!.day.jobs[0].phone).toBe("+60312735359");
    expect(cached!.fetchedAt).toMatch(/^\d{4}-\d{2}-\d{2}T/);
  });

  it("refuses to hand back yesterday's day as today's", () => {
    // The dangerous case. Yesterday's jobs look exactly like a real day, and
    // a technician would drive to the first address on it. Showing nothing is
    // strictly better than showing something plausible and wrong.
    saveDay(day("2026-09-02"));
    expect(loadDay("2026-09-03", 1)).toBeNull();
  });

  it("returns nothing when there is nothing", () => {
    expect(loadDay("2026-09-03", 1)).toBeNull();
  });

  it("survives a corrupted entry rather than throwing", () => {
    window.localStorage.setItem("waypoint.field.day", "{not json");
    expect(loadDay("2026-09-03", 1)).toBeNull();
  });

  it("rejects an entry that is missing the jobs it promises", () => {
    window.localStorage.setItem(
      "waypoint.field.day",
      JSON.stringify({ day: { day: "2026-09-03" }, fetchedAt: "x" }),
    );
    expect(loadDay("2026-09-03", 1)).toBeNull();
  });

  it("refuses to hand one technician another technician's day", () => {
    // Two people sharing a van and a spare handset is not hypothetical.
    // clearSession() already wipes this on sign out, so this should never
    // fire -- which is exactly why it is here. Everywhere else in the system
    // scoping is structural; a cache keyed only by date would be the one
    // place it could leak, on the first frame, before any request was made.
    saveDay(day("2026-09-03")); // technician_id 1
    expect(loadDay("2026-09-03", 2)).toBeNull();
    expect(loadDay("2026-09-03", 1)).not.toBeNull();
  });

  it("is gone after signing out", async () => {
    const { clearSession } = await import("@/lib/session");
    saveDay(day("2026-09-03"));
    clearSession();
    expect(loadDay("2026-09-03", 1)).toBeNull();
  });
});

describe("which day it is", () => {
  it("is the Malaysian date, not the device's", () => {
    // 2026-09-03 17:30 UTC is already the 4th in Malaysia (UTC+8). A phone
    // left on UTC -- which is what a factory reset gives you -- must still
    // ask for the day the technician is actually working.
    expect(localDay(new Date("2026-09-03T17:30:00Z"))).toBe("2026-09-04");
  });

  it("does not roll over early", () => {
    expect(localDay(new Date("2026-09-03T15:59:00Z"))).toBe("2026-09-03");
  });

  it("formats as the API expects", () => {
    expect(localDay(new Date("2026-01-05T02:00:00Z"))).toBe("2026-01-05");
  });
});
