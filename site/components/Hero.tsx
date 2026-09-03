import DispatchView from "./DispatchView";
import { Container, PrimaryLink, SecondaryLink } from "./ui";

const FACTS = [
  { label: "OR-Tools CP-SAT", dot: "bg-accent" },
  { label: "OpenStreetMap road times", dot: "bg-amber-ink" },
  { label: "Every schedule independently checked", dot: "bg-sky-ink" },
];

export default function Hero() {
  return (
    <section
      id="top"
      aria-labelledby="hero-heading"
      className="pt-14 sm:pt-20"
    >
      <Container>
        <p className="font-mono text-[11px] uppercase tracking-[0.14em] text-muted">
          Field service dispatch
        </p>

        <h1
          id="hero-heading"
          className="mt-5 max-w-4xl text-balance text-[34px] font-medium leading-[1.08] tracking-[-0.03em] sm:text-[52px]"
        >
          Forty jobs, eight technicians, one plan that holds.
        </h1>

        <p className="mt-6 max-w-2xl text-pretty text-[17px] leading-[1.9] text-ink-2">
          Waypoint assigns and routes your whole day against{" "}
          <span className="hl bg-amber-tint text-ink">real road travel times</span>{" "}
          &mdash; skills, customer windows, shifts and van stock included &mdash;
          then{" "}
          <span className="hl bg-sky-tint text-ink">re-solves in seconds</span>{" "}
          when the day changes.
        </p>

        <div className="mt-8 flex flex-wrap items-center gap-3">
          <PrimaryLink href="#book-a-demo">Book a demo</PrimaryLink>
          <SecondaryLink href="#benchmark">See the benchmark</SecondaryLink>
        </div>

        <ul className="mt-8 flex flex-wrap items-center gap-x-5 gap-y-2 text-[12px] text-muted">
          {FACTS.map((fact) => (
            <li key={fact.label} className="flex items-center gap-2">
              <span
                aria-hidden="true"
                className={`size-1.5 rounded-full ${fact.dot}`}
              />
              {fact.label}
            </li>
          ))}
        </ul>
      </Container>

      <Container className="mt-12 sm:mt-16">
        <DispatchView />
        <p className="mt-3 text-[12px] text-muted">
          The dispatch view: one colour per technician, every stop in visit
          order, and the same day laid out on the timeline below.
        </p>
      </Container>
    </section>
  );
}
