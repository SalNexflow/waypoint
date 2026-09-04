import DemoForm from "./DemoForm";
import { Container } from "./ui";

const POINTS = [
  "Thirty minutes, screen-shared, on your data if you have a day’s jobs to hand.",
  "We plan one of your real days live and walk through what the solver did and why.",
  "Then we break it — pull a technician out at midday and re-solve in front of you.",
];

export default function CTA() {
  return (
    <section
      id="book-a-demo"
      aria-labelledby="cta-heading"
      className="relative isolate overflow-hidden border-t border-line py-20 sm:py-28"
    >
      <div aria-hidden="true" className="wash absolute inset-0 -z-10 scale-x-[-1]" />
      <Container>
        <div className="grid gap-12 lg:grid-cols-[1fr_420px] lg:gap-20">
          <div>
            <p className="flex items-center gap-3 font-mono text-[11px] uppercase tracking-[0.14em] text-muted">
              <span aria-hidden="true">07</span>
              <span aria-hidden="true" className="h-px w-6 bg-line-strong" />
              <span>Book a demo</span>
            </p>

            <h2
              id="cta-heading"
              className="mt-5 max-w-xl text-pretty text-2xl font-medium leading-[1.15] tracking-[-0.02em] sm:text-[32px]"
            >
              See your own day solved, then watch it survive a sick call.
            </h2>

            <ul className="mt-8 space-y-4">
              {POINTS.map((point) => (
                <li key={point} className="flex gap-3 text-[15px] leading-relaxed text-ink-2">
                  <span
                    aria-hidden="true"
                    className="mt-2 size-1.5 shrink-0 rounded-full bg-accent"
                  />
                  {point}
                </li>
              ))}
            </ul>

            {/* There is no hosted instance to click through to. Saying so here
                is better than a "Try it" button that opens something broken:
                the routing engine alone wants a 673MB graph resident in
                memory, which is not a thing to leave running on a free tier
                for passers-by. Anyone technical enough to want a look can have
                the whole stack instead, live OSRM included. */}
            <div className="mt-10 rounded-lg border border-line bg-surface/70 p-5">
              <p className="font-mono text-[10px] uppercase tracking-[0.14em] text-muted">
                Or run it yourself
              </p>
              <p className="mt-2.5 text-[15px] leading-relaxed text-ink-2">
                There is no hosted demo. The whole system — solver, routing
                engine, dispatcher console and technician app — runs locally
                with{" "}
                <code className="whitespace-nowrap rounded border border-line bg-sunken px-1.5 py-0.5 font-mono text-[13px] text-ink">
                  docker compose up
                </code>
                , on real Klang Valley road times rather than a canned
                recording.{" "}
                <a
                  href="https://github.com/SalNexflow/waypoint"
                  className="font-medium text-accent underline decoration-line-strong underline-offset-2 transition-colors hover:decoration-accent"
                  rel="noreferrer"
                >
                  Source and setup on GitHub
                </a>
                .
              </p>
            </div>
          </div>

          <div className="rounded-lg border border-line bg-surface p-6 sm:p-7">
            <DemoForm />
          </div>
        </div>
      </Container>
    </section>
  );
}
