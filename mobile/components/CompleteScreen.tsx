"use client";

import { useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import { partLabel } from "@/lib/format";
import { submitCompletion } from "@/lib/completion";
import { type Downscaled, downscale, sizeLabel } from "@/lib/photo";
import { useDay } from "@/lib/use-day";

/**
 * Complete -- screen 3 of 4.
 *
 * Deliberately short, and the reason is in the spec: if this takes too long
 * people stop filling it in and the data becomes worthless. Three things and
 * a button. No confirmation dialog, no review step.
 *
 * The finish time is stamped when this screen OPENS, not when Done is tapped.
 * The technician finished the job and then filled in a form; the minute of
 * typing is not part of the job, and phase 9 re-plans the rest of the day off
 * that number.
 */
export function CompleteScreen({ jobId }: { jobId: number | null }) {
  const router = useRouter();
  const { auth, state, refreshLocal } = useDay();

  // Stamped once, on mount. useRef rather than useState because nothing should
  // re-render when it is set, and it must survive every keystroke in the notes
  // field without being recomputed.
  const finishedAt = useRef(new Date().toISOString());

  const [parts, setParts] = useState<Set<string> | null>(null);
  const [notes, setNotes] = useState("");
  const [photo, setPhoto] = useState<Downscaled | null>(null);
  const [photoError, setPhotoError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [showAll, setShowAll] = useState(false);

  const day = state.kind === "ready" ? state.day : null;
  const job = day?.jobs.find((j) => j.id === jobId) ?? null;

  // Prefilled with what was PLANNED, which is right nearly every time. The
  // technician's job here is to correct the exceptions, not to re-enter what
  // the schedule already knew.
  useEffect(() => {
    if (job && parts === null) setParts(new Set(job.parts));
  }, [job, parts]);

  // A preview URL is a handle on a blob the browser keeps alive until it is
  // revoked. Leaving them is a leak that only shows up after a long shift.
  useEffect(() => {
    return () => {
      if (photo) URL.revokeObjectURL(photo.previewUrl);
    };
  }, [photo]);

  if (auth !== "in" || state.kind === "waiting") {
    return (
      <main className="mx-auto flex min-h-dvh w-full max-w-md items-center justify-center">
        <p className="text-[1.5rem] font-bold text-ink-soft">Waypoint</p>
      </main>
    );
  }

  if (!job || !day) {
    return (
      <main className="mx-auto flex min-h-dvh w-full max-w-md flex-col px-4 pt-8">
        <p className="text-[1.3rem] font-bold">That job isn&rsquo;t on your day</p>
        <button
          data-tappable
          type="button"
          onClick={() => router.push("/")}
          className="mt-5 min-h-[3.75rem] w-full rounded-xl bg-now text-[1.1rem] font-bold text-now-ink"
        >
          Back to today
        </button>
      </main>
    );
  }

  if (job.completed) {
    // Already filled in. Submitting again would be discarded server-side --
    // job_id is the primary key of job_completions -- and silently accepting
    // a form whose contents go nowhere is worse than saying so.
    return (
      <main className="mx-auto flex min-h-dvh w-full max-w-md flex-col px-4 pt-8">
        <p className="text-[1.3rem] font-bold">Already completed</p>
        <p className="mt-2 text-[1.05rem] text-ink-soft">
          {job.customer} was written up earlier. Ring dispatch if something
          needs changing.
        </p>
        <button
          data-tappable
          type="button"
          onClick={() => router.push("/")}
          className="mt-5 min-h-[3.75rem] w-full rounded-xl bg-now text-[1.1rem] font-bold text-now-ink"
        >
          Back to today
        </button>
      </main>
    );
  }

  const chosen = parts ?? new Set<string>();
  const extras = day.parts_catalogue.filter((p) => !job.parts.includes(p));

  function toggle(part: string) {
    setParts((prev) => {
      const next = new Set(prev ?? []);
      if (next.has(part)) next.delete(part);
      else next.add(part);
      return next;
    });
  }

  async function pickPhoto(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    // Cleared so picking the same file twice still fires a change event.
    e.target.value = "";
    if (!file) return;

    setPhotoError(null);
    try {
      const next = await downscale(file);
      setPhoto((prev) => {
        if (prev) URL.revokeObjectURL(prev.previewUrl);
        return next;
      });
    } catch {
      setPhotoError("Couldn't read that photo. Try taking it again.");
    }
  }

  async function done() {
    if (busy || !job) return;
    setBusy(true);
    await submitCompletion({
      jobId: job.id,
      partsUsed: [...chosen],
      notes,
      photoBase64: photo?.base64 ?? null,
      finishedAt: finishedAt.current,
    });
    refreshLocal();
    // Straight back to the day. No "saved!" screen -- the job moving into the
    // done group on Today is the confirmation, and it is the one they were
    // heading to anyway.
    router.push("/");
  }

  return (
    <main className="mx-auto flex min-h-dvh w-full max-w-md flex-col">
      <header className="sticky top-0 z-10 flex items-center border-b border-line bg-paper px-2 py-2">
        <button
          data-tappable
          type="button"
          onClick={() => router.back()}
          className="flex min-h-[3.25rem] items-center gap-1 rounded-lg px-3 text-[1.05rem] font-semibold"
        >
          <span aria-hidden>←</span> Back
        </button>
        <p className="ml-2 min-w-0 flex-1 truncate text-[1.05rem] font-bold">
          {job.customer}
        </p>
      </header>

      <div className="flex flex-col gap-4 px-4 pt-4">
        {/* --- Parts --- */}
        <section>
          <h2 className="text-[0.8rem] font-bold tracking-[0.08em] text-ink-soft uppercase">
            Parts used
          </h2>
          <div className="mt-2 flex flex-col gap-2">
            {job.parts.map((part) => (
              <PartToggle
                key={part}
                part={part}
                on={chosen.has(part)}
                onToggle={() => toggle(part)}
              />
            ))}
            {job.parts.length === 0 && !showAll ? (
              <p className="text-[1rem] text-ink-soft">None were planned.</p>
            ) : null}

            {/* Extras behind one tap. Most jobs use what was planned, and a
                list of eight checkboxes on every completion is exactly the
                friction that stops people filling this in. */}
            {showAll
              ? extras.map((part) => (
                  <PartToggle
                    key={part}
                    part={part}
                    on={chosen.has(part)}
                    onToggle={() => toggle(part)}
                  />
                ))
              : null}
          </div>

          {!showAll && extras.length > 0 ? (
            <button
              data-tappable
              type="button"
              onClick={() => setShowAll(true)}
              className="mt-2 min-h-[3rem] text-[1rem] font-semibold text-now underline underline-offset-4"
            >
              + Used something else
            </button>
          ) : null}
        </section>

        {/* --- Notes --- */}
        <section>
          <label
            htmlFor="notes"
            className="text-[0.8rem] font-bold tracking-[0.08em] text-ink-soft uppercase"
          >
            Notes
          </label>
          <textarea
            id="notes"
            value={notes}
            onChange={(e) => setNotes(e.target.value)}
            rows={4}
            maxLength={2000}
            placeholder="Anything the office should know."
            className="mt-2 w-full rounded-xl border-2 border-line bg-paper px-4 py-3 text-[1.05rem] leading-snug focus:border-now focus:outline-none"
          />
        </section>

        {/* --- Photo --- */}
        <section>
          <h2 className="text-[0.8rem] font-bold tracking-[0.08em] text-ink-soft uppercase">
            Photo
          </h2>
          {photo ? (
            <div className="mt-2 flex items-center gap-3">
              {/* eslint-disable-next-line @next/next/no-img-element --
                  next/image optimises remote and bundled images; this is a
                  blob URL for a file that exists only on this device. */}
              <img
                src={photo.previewUrl}
                alt="Photo of the finished job"
                className="h-24 w-24 rounded-xl border border-line object-cover"
              />
              <div>
                <p className="text-[0.95rem] font-semibold">
                  {sizeLabel(photo.bytes)}
                </p>
                <button
                  data-tappable
                  type="button"
                  onClick={() => {
                    URL.revokeObjectURL(photo.previewUrl);
                    setPhoto(null);
                  }}
                  className="min-h-[3rem] text-[1rem] font-semibold text-alert underline underline-offset-4"
                >
                  Remove
                </button>
              </div>
            </div>
          ) : (
            <label
              data-tappable
              className="mt-2 flex min-h-[4rem] w-full items-center justify-center rounded-xl border-2 border-now bg-paper text-[1.1rem] font-bold text-now"
            >
              Take a photo
              {/* capture="environment" asks for the rear camera directly
                  rather than the gallery picker. accept is still needed:
                  without it some Android browsers offer file managers too. */}
              <input
                type="file"
                accept="image/*"
                capture="environment"
                onChange={pickPhoto}
                className="sr-only"
              />
            </label>
          )}
          {photoError ? (
            <p role="alert" className="mt-2 text-[0.95rem] font-semibold text-alert">
              {photoError}
            </p>
          ) : null}
        </section>

        <div className="h-[6.5rem]" aria-hidden />
      </div>

      <div className="fixed inset-x-0 bottom-0 border-t border-line bg-paper px-4 pt-3 pb-[calc(0.75rem+env(safe-area-inset-bottom))]">
        <div className="mx-auto w-full max-w-md">
          <button
            data-tappable
            type="button"
            onClick={done}
            disabled={busy}
            className="min-h-[4.75rem] w-full rounded-xl bg-done text-[1.25rem] font-bold text-white disabled:opacity-50"
          >
            {busy ? "Saving…" : "Done"}
          </button>
        </div>
      </div>
    </main>
  );
}

function PartToggle({
  part,
  on,
  onToggle,
}: {
  part: string;
  on: boolean;
  onToggle: () => void;
}) {
  return (
    <button
      data-tappable
      type="button"
      role="checkbox"
      aria-checked={on}
      onClick={onToggle}
      // A full-width row, not a checkbox with a label beside it. The target is
      // the whole thing, and a native checkbox is about 4mm across.
      className={`flex min-h-[3.75rem] items-center gap-3 rounded-xl border-2 px-4 text-left text-[1.05rem] font-semibold ${
        on ? "border-now bg-now text-now-ink" : "border-line bg-paper"
      }`}
    >
      <span
        aria-hidden
        className={`flex size-6 shrink-0 items-center justify-center rounded border-2 ${
          on ? "border-white bg-white text-now" : "border-ink-soft"
        }`}
      >
        {on ? "✓" : ""}
      </span>
      {partLabel(part)}
    </button>
  );
}
