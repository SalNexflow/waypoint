import Image from "next/image";
import { Container, SectionHeading, SectionLabel } from "./ui";

/**
 * The second product surface.
 *
 * The screenshots are real captures of the technician PWA (mobile/) running a
 * production build against the API, at 390x844 and 3x, so they hold up at the
 * size they are shown. Re-take them with scratchpad/pwa-shots.js — or any
 * equivalent — rather than mocking a phone screen here.
 */

const POINTS = [
  {
    title: "The whole day, in order",
    body: "A technician opens the app to their own day already sorted: every job in visit order with the address, the promised window, the parts the van should be carrying and a number to call the customer on.",
  },
  {
    title: "Navigation is one tap",
    body: "Navigate hands the address straight to Waze or Google Maps. Nobody copies an address between two apps while parked at the kerb.",
  },
  {
    title: "It works with no signal",
    body: "Status updates are written to the phone first and sent when the connection returns. A basement, a lift shaft or an underground car park costs nothing — the strip just says how many updates are still waiting.",
  },
  {
    title: "Changes arrive without a phone call",
    body: "When the day is re-solved, the technician sees what moved and what is no longer theirs on their own screen, before they drive to it.",
  },
];

const SHOTS = [
  {
    src: "/app/today.png",
    caption: "Today",
    alt: "The technician app's Today screen: the technician's name, seven jobs left finishing around 17:00, the next job as a large card, and the rest of the day listed by arrival time.",
    offset: "lg:translate-y-0",
  },
  {
    src: "/app/job-detail.png",
    caption: "Job detail",
    alt: "A job screen showing the site name and full address, Navigate and call buttons, the promised window of 08:45 to 10:45, planned arrival 08:06, the reported fault, the parts needed, and an On my way button.",
    offset: "lg:translate-y-10",
  },
  {
    src: "/app/complete.png",
    caption: "Complete",
    alt: "The completion form: parts used prefilled with the planned part, a notes field, a photo button, and a Done button.",
    offset: "lg:translate-y-2",
  },
  {
    src: "/app/offline.png",
    caption: "Offline",
    alt: "The Today screen with no connection: a strip reading Offline, updated 09:12, 3 waiting to send, above a job marked On my way.",
    offset: "lg:translate-y-12",
  },
];

export default function TechnicianApp() {
  return (
    <section
      id="technician-app"
      aria-labelledby="technician-app-heading"
      className="overflow-hidden border-t border-line bg-surface py-20 sm:py-28"
    >
      <Container>
        <SectionLabel index="04">The technician app</SectionLabel>
        <SectionHeading id="technician-app-heading">
          A schedule is only real once it reaches the person driving to the job.
        </SectionHeading>
        <p className="mt-5 max-w-2xl text-[15px] leading-relaxed text-ink-2">
          Dispatch is half of it. The other half is an installable app the
          technician carries: their day, one job at a time, that keeps working
          when the signal does not.
        </p>

        <div className="mt-14 grid gap-x-16 gap-y-10 sm:grid-cols-2">
          {POINTS.map((point) => (
            <div key={point.title} className="flex gap-3.5">
              <span
                aria-hidden="true"
                className="mt-2 size-1.5 shrink-0 rounded-full bg-accent"
              />
              <div>
                <h3 className="text-[16px] font-medium leading-snug tracking-[-0.01em]">
                  {point.title}
                </h3>
                <p className="mt-2 text-[14px] leading-relaxed text-ink-2">
                  {point.body}
                </p>
              </div>
            </div>
          ))}
        </div>
      </Container>

      {/* The phones. A scrolling strip on a phone, a staggered overlapping row
          on a wide screen — big enough to read, which is the whole point of
          showing them. */}
      <div className="mt-16 sm:mt-20">
        {/* Scrollable on a narrow screen, so it needs to be reachable by
            keyboard as well as by thumb. */}
        <ul
          tabIndex={0}
          aria-label="Screens from the technician app"
          className="flex snap-x snap-mandatory gap-5 overflow-x-auto px-5 pb-4 sm:px-8 lg:mx-auto lg:w-max lg:snap-none lg:gap-4 lg:overflow-visible lg:px-0 lg:pb-16"
        >
          {SHOTS.map((shot) => (
            <li
              key={shot.src}
              className={`w-[248px] shrink-0 snap-center sm:w-[268px] lg:w-[272px] ${shot.offset}`}
            >
              <figure>
                {/* A thin ink bezel, the same hairline language as the
                    dispatch card. No hardware drawing, no glare. */}
                <div className="overflow-hidden rounded-[1.6rem] border border-line-strong bg-ink p-1.5 shadow-[0_1px_2px_rgba(16,17,19,0.04),0_18px_50px_-20px_rgba(16,17,19,0.28)]">
                  <Image
                    src={shot.src}
                    alt={shot.alt}
                    width={1170}
                    height={2532}
                    sizes="(min-width: 1024px) 272px, 268px"
                    className="w-full rounded-[1.2rem]"
                  />
                </div>
                <figcaption className="mt-3 text-center font-mono text-[11px] uppercase tracking-[0.14em] text-muted">
                  {shot.caption}
                </figcaption>
              </figure>
            </li>
          ))}
        </ul>
      </div>

      <Container>
        <p className="mt-6 max-w-2xl border-l-2 border-accent pl-5 text-[15px] leading-relaxed text-ink-2 sm:mt-10">
          It matters to the office as much as to the van: the completion times
          technicians actually report feed straight back into re-planning. A job
          that runs long is absorbed by the rest of the day automatically,
          instead of someone rebuilding the afternoon by hand.
        </p>
      </Container>
    </section>
  );
}
