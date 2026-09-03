import { Container, SectionHeading, SectionLabel } from "./ui";

/*
  Written quotes with no names against them yet. Each is what we would want a
  first customer to be able to say, in their words rather than ours — send them
  to the first deployments and let people correct them.

  To publish one, put the real name and company on it and drop `draft`:

  {
    quote: "Waypoint cut our morning planning from an hour to five minutes.",
    name: "Aisyah Rahman, Operations Manager",
    company: "Northwind Cooling",
  }

  Keep quotes to two or three sentences so the three cards stay the same
  height. `draft: true` is what shows the pending label and dims the empty
  attribution; nothing here carries a person's name until that person has
  actually said it.
*/
const TESTIMONIALS = [
  {
    quote:
      "The morning plan used to be an hour of my day, and it was out of date by ten. Now I look at what Waypoint has produced, move a couple of jobs by hand, and the vans are out before eight.",
    name: "Name, Role",
    company: "Company",
    draft: true,
  },
  {
    quote:
      "A technician called in sick at twenty to eight. By the time I had finished the call the day had been re-solved around him, and we only had to phone a handful of customers instead of half the list.",
    name: "Name, Role",
    company: "Company",
    draft: true,
  },
  {
    quote:
      "The technicians stopped ringing the office to ask what was next. They open the app, the day is in order, and it keeps working in the basement car parks where we always lost signal.",
    name: "Name, Role",
    company: "Company",
    draft: true,
  },
];

export default function Testimonials() {
  return (
    <section
      aria-labelledby="testimonials-heading"
      className="border-t border-line bg-surface py-20 sm:py-28"
    >
      <Container>
        <SectionLabel index="06">Testimonials</SectionLabel>
        <SectionHeading id="testimonials-heading">
          Three slots, held for the first teams to run a live day on Waypoint.
        </SectionHeading>
        <p className="mt-5 max-w-2xl text-[15px] leading-relaxed text-ink-2">
          Waypoint has been benchmarked, not yet deployed. The quotes below are
          drafts — what we would want a first customer to be able to say. Each
          one gets a name against it when someone has actually said it, and not
          before.
        </p>

        <ul className="mt-14 grid gap-5 sm:grid-cols-2 lg:grid-cols-3">
          {TESTIMONIALS.map((item, i) => (
            <li
              key={i}
              className={
                item.draft
                  ? "flex flex-col rounded-md border border-dashed border-line-strong bg-paper p-6"
                  : "flex flex-col rounded-md border border-line bg-paper p-6"
              }
            >
              {item.draft ? (
                <p className="mb-4 font-mono text-[10px] uppercase tracking-[0.14em] text-muted">
                  Draft &middot; not yet attributed
                </p>
              ) : null}

              <blockquote className="grow text-[15px] leading-relaxed text-ink-2 sm:min-h-[7.5rem]">
                {`“${item.quote}”`}
              </blockquote>

              {/* The attribution is the part still empty, so it is the part
                  that reads as empty. Its text starts from the ink colour so
                  dimming stays above the 4.5:1 contrast floor. */}
              <div
                className={`mt-6 flex items-center gap-3 border-t border-line pt-5 ${
                  item.draft ? "opacity-70" : ""
                }`}
              >
                <span
                  aria-hidden="true"
                  className={
                    item.draft
                      ? "size-9 shrink-0 rounded-full border border-dashed border-line-strong bg-sunken"
                      : "size-9 shrink-0 rounded-full bg-sunken"
                  }
                />
                <span className="min-w-0">
                  <span className="block truncate text-[14px] font-medium text-ink">
                    {item.name}
                  </span>
                  <span
                    className={`block truncate text-[13px] ${
                      item.draft ? "text-ink" : "text-muted"
                    }`}
                  >
                    {item.company}
                  </span>
                </span>
              </div>
            </li>
          ))}
        </ul>
      </Container>
    </section>
  );
}
