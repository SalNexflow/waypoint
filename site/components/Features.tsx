import type { ReactNode } from "react";
import { Container, SectionHeading, SectionLabel } from "./ui";

/* Line icons, 20px, one stroke weight. Drawn rather than imported so the whole
   set stays consistent and the page ships no icon dependency. */

const stroke = {
  fill: "none",
  stroke: "currentColor",
  strokeWidth: 1.4,
  strokeLinecap: "round" as const,
  strokeLinejoin: "round" as const,
};

/** Each card carries one hue: a pale plate with the matching ink on top. */
const TONES = {
  blue: "bg-accent/10 text-accent",
  amber: "bg-amber-tint text-amber-ink",
  sky: "bg-sky-tint text-sky-ink",
  violet: "bg-violet-tint text-violet-ink",
  rose: "bg-rose-tint text-rose-ink",
} as const;

function Icon({
  tone,
  children,
}: {
  tone: keyof typeof TONES;
  children: ReactNode;
}) {
  return (
    <span
      className={`inline-flex size-9 items-center justify-center rounded-md ${TONES[tone]}`}
    >
      <svg width="20" height="20" viewBox="0 0 20 20" aria-hidden="true">
        {children}
      </svg>
    </span>
  );
}

const FEATURES = [
  {
    icon: (
      <Icon tone="blue">
        <rect x="2.5" y="2.5" width="15" height="15" rx="2" {...stroke} />
        <path d="M2.5 7.5h15M7.5 7.5v10M12.5 2.5v5" {...stroke} />
        <path d="M9.5 12.5l1.6 1.6 3-3.2" {...stroke} />
      </Icon>
    ),
    title: "Constraint-based scheduling",
    body: "Skills, customer time windows, shift start and end, service durations and travel all live in one model as hard constraints, not as filters applied after a route is drawn. A schedule is either feasible or it is not, and the solver will tell you which jobs it could not place and what stopped it.",
  },
  {
    icon: (
      <Icon tone="amber">
        <path d="M3.6 15.6c2.6-.6 3.3-2.6 2.2-4.1C4.6 9.9 5.6 8 8.2 7.6l4-.6c2.2-.35 3-1.6 2.6-3" {...stroke} />
        <circle cx="3.6" cy="15.6" r="1.7" {...stroke} />
        <circle cx="15" cy="3.6" r="1.7" {...stroke} />
        <circle cx="8.6" cy="11.2" r="1.1" {...stroke} />
        <circle cx="13.2" cy="7.2" r="1.1" {...stroke} />
      </Icon>
    ),
    title: "Real road travel times",
    body: "Every leg is costed against a routing graph built from OpenStreetMap data for your service area. Straight-line estimates understate real urban drive time badly enough that a day planned on them starts running late before lunch.",
  },
  {
    icon: (
      <Icon tone="sky">
        <path d="M10 2.5l6 3v4.2c0 3.6-2.4 6.6-6 7.8-3.6-1.2-6-4.2-6-7.8V5.5z" {...stroke} />
        <path d="M7.4 10.1l1.8 1.8 3.6-3.8" {...stroke} />
      </Icon>
    ),
    title: "Skill and parts matching",
    body: "A job is only assigned to a technician who holds the certifications it requires and has the parts on the van. Chiller work does not land with a split-unit installer, and nobody drives across town to discover the compressor is in someone else’s van.",
  },
  {
    icon: (
      <Icon tone="violet">
        <path d="M16.5 8A6.5 6.5 0 0 0 4.6 5.4" {...stroke} />
        <path d="M3.5 12a6.5 6.5 0 0 0 11.9 2.6" {...stroke} />
        <path d="M4.6 2.2v3.2h3.2M15.4 17.8v-3.2h-3.2" {...stroke} />
      </Icon>
    ),
    title: "Mid-day re-optimisation",
    body: "When something changes, completed and in-progress work is pinned and only the remainder is re-solved. In benchmarking an 80-job day, pinning cost 459 extra minutes of driving and saved 57 customer phone calls — five calls instead of sixty-two.",
  },
  {
    icon: (
      <Icon tone="rose">
        <path d="M3 4.5h14v9H8.5L4.5 17v-3.5H3z" {...stroke} />
        <path d="M6.5 8h7M6.5 10.6h4.5" {...stroke} />
      </Icon>
    ),
    title: "Plain-English dispatch changes",
    body: 'Type “Aisyah is out from midday, move her afternoon to whoever is closest”. Waypoint turns it into a typed, validated change, shows you the cost and the customers affected, and applies it only when you say so.',
  },
  {
    icon: (
      <Icon tone="blue">
        <rect x="3" y="2.5" width="14" height="15" rx="2" {...stroke} />
        <path d="M6.5 7.5l1.5 1.5 3-3.2M6.5 13l1.5 1.5 3-3.2" {...stroke} />
        <path d="M13 8h1.5M13 13.5h1.5" {...stroke} />
      </Icon>
    ),
    title: "Independent validation",
    body: "Every schedule is re-checked by a verifier written separately from the solver: no overlapping visits, no missed windows, no missing skills, no travel time that does not exist. All 45 runs in the benchmark passed it.",
  },
];

export default function Features() {
  return (
    <section
      id="features"
      aria-labelledby="features-heading"
      className="border-t border-line py-20 sm:py-28"
    >
      <Container>
        <SectionLabel index="03">Features</SectionLabel>
        <SectionHeading id="features-heading">
          Built for the constraints a real service day actually has.
        </SectionHeading>

        <div className="mt-14 grid gap-px border border-line bg-line sm:grid-cols-2 lg:grid-cols-3">
          {FEATURES.map((feature) => (
            <article key={feature.title} className="bg-paper p-6 sm:p-7">
              {feature.icon}
              <h3 className="mt-4 text-[16px] font-medium leading-snug tracking-[-0.01em]">
                {feature.title}
              </h3>
              <p className="mt-2.5 text-[14px] leading-relaxed text-ink-2">
                {feature.body}
              </p>
            </article>
          ))}
        </div>
      </Container>
    </section>
  );
}
