import { Container, SectionHeading, SectionLabel } from "./ui";

const STEPS = [
  {
    index: "01",
    step: "Jobs come in",
    tag: "Intake",
    band: "bg-amber",
    body: "Push the day’s work in from your existing system or over the API. Each job carries an address, a service duration, the customer’s time window, the skills it needs and the parts that have to be on the van.",
    aside: [
      ["Job", "J-1207 · Ampang"],
      ["Window", "09:00 – 12:00"],
      ["Duration", "60 min"],
      ["Skills", "vrv_vrf, electrical"],
      ["Parts", "pcb_board"],
    ],
  },
  {
    index: "02",
    step: "Waypoint solves the day",
    tag: "Solve",
    band: "bg-sky",
    body: "A constraint solver searches assignments and route orderings together, costing every leg with real road travel times. You set the time budget; it returns the best schedule it can prove within it, and tells you which jobs it had to hold back and why.",
    aside: [
      ["Assigned", "43 of 45"],
      ["Travel", "12.5 min per job"],
      ["Windows met", "42 of 43"],
      ["Held back", "2 · reason given"],
      ["Solve time", "1.8 s"],
    ],
  },
  {
    index: "03",
    step: "Changes get absorbed",
    tag: "Re-solve",
    band: "bg-violet",
    body: "A sick call, a breakdown, an urgent job at eleven. Waypoint pins what is already done or in progress and re-solves only the remainder, so the morning stands. You see the cost and the affected customers before anything is committed.",
    aside: [
      ["Change", "Aisyah unavailable from 12:00"],
      ["Pinned", "18 completed, 3 in progress"],
      ["Re-solved", "24 remaining jobs"],
      ["Customers to call", "5"],
      ["Re-solve time", "2.4 s"],
    ],
  },
];

export default function HowItWorks() {
  return (
    <section
      id="how-it-works"
      aria-labelledby="how-it-works-heading"
      className="border-t border-line bg-surface pt-20 sm:pt-28"
    >
      <Container>
        <SectionLabel index="02">How it works</SectionLabel>
        <SectionHeading id="how-it-works-heading">
          Three steps, and the third is the one that matters most.
        </SectionHeading>
      </Container>

      {/* Full-width colour bands, stacked with no gap between them. */}
      <ol className="mt-14">
        {STEPS.map((step) => (
          <li key={step.step} className={step.band}>
            <Container className="grid gap-8 py-10 sm:py-14 lg:grid-cols-[1fr_380px] lg:items-start lg:gap-16">
              <div>
                <p className="flex items-center gap-3 font-mono text-[12px] uppercase tracking-[0.14em] text-ink/75">
                  <span>{step.index}</span>
                  <span aria-hidden="true">&rarr;</span>
                  <span>{step.tag}</span>
                </p>
                <h3 className="mt-4 max-w-xl text-[24px] font-medium leading-[1.15] tracking-[-0.02em] text-ink sm:text-[30px]">
                  {step.step}
                </h3>
                <p className="mt-4 max-w-xl text-[15px] leading-relaxed text-ink/80">
                  {step.body}
                </p>
              </div>

              <dl className="h-fit divide-y divide-ink/10 rounded-md bg-surface/85 px-4 py-1 text-[13px]">
                {step.aside.map(([k, v]) => (
                  <div
                    key={k}
                    className="flex items-baseline justify-between gap-4 py-2.5"
                  >
                    <dt className="shrink-0 text-muted">{k}</dt>
                    <dd className="tabular text-right font-mono text-[12px] text-ink">
                      {v}
                    </dd>
                  </div>
                ))}
              </dl>
            </Container>
          </li>
        ))}
      </ol>
    </section>
  );
}
