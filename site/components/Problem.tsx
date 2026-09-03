import { Container, SectionHeading, SectionLabel } from "./ui";

const BLOCKS = [
  {
    band: "bg-amber",
    figure: "text-amber-ink",
    stat: "~60 min",
    statLabel: "before the first van moves",
    title: "The day is planned by hand, every morning",
    body: "A dispatcher works out who is near what, who holds the right ticket, and who can still make a two o’clock window. It takes about an hour, it happens before anyone is on the road, and it has to be redone the moment something moves.",
  },
  {
    band: "bg-sky",
    figure: "text-sky-ink",
    stat: "40 jobs",
    statLabel: "across 8 technicians",
    title: "Nobody can route 40 jobs across 8 technicians",
    body: "The number of ways to split a day’s jobs between a team and order each route is far larger than any person can search. Experienced dispatchers produce workable days, not good ones, and the gap is paid in fuel, overtime and windows that get missed.",
  },
  {
    band: "bg-rose",
    figure: "text-rose-ink",
    stat: "1 call",
    statLabel: "at 07:40",
    title: "One sick technician and the day is rebuilt",
    body: "A technician calls in sick and a dozen jobs land back on the table. Reassigning them means touching everyone else’s route, so the fastest safe answer is usually to phone customers and move them to another day.",
  },
];

export default function Problem() {
  return (
    <section
      id="problem"
      aria-labelledby="problem-heading"
      className="border-t border-line py-20 sm:py-28"
    >
      <Container>
        <SectionLabel index="01">The problem</SectionLabel>
        <SectionHeading id="problem-heading">
          Planning a service day by hand is slow, and the plan is out of date by
          mid-morning.
        </SectionHeading>

        <div className="mt-14 grid gap-px border border-line bg-line sm:grid-cols-3">
          {BLOCKS.map((block) => (
            <article key={block.title} className="bg-paper">
              <div aria-hidden="true" className={`h-1.5 ${block.band}`} />
              <div className="p-6 sm:p-7">
              <p
                className={`tabular font-mono text-[22px] font-medium leading-none tracking-[-0.02em] ${block.figure}`}
              >
                {block.stat}
              </p>
              <p className="mt-2 text-[12px] uppercase tracking-[0.08em] text-muted">
                {block.statLabel}
              </p>
              <h3 className="mt-6 text-[16px] font-medium leading-snug tracking-[-0.01em]">
                {block.title}
              </h3>
              <p className="mt-3 text-[14px] leading-relaxed text-ink-2">
                {block.body}
              </p>
              </div>
            </article>
          ))}
        </div>
      </Container>
    </section>
  );
}
