import { Container } from "./ui";

/** The mark: two stops and the leg between them. */
export function Logotype() {
  return (
    <span className="flex items-center gap-2">
      <svg
        width="18"
        height="18"
        viewBox="0 0 18 18"
        aria-hidden="true"
        className="shrink-0"
      >
        <path
          d="M3.5 13.5 8 5.5l6.5 3"
          fill="none"
          stroke="#2563eb"
          strokeWidth="1.6"
          strokeLinecap="round"
          strokeLinejoin="round"
        />
        <circle cx="3.5" cy="13.5" r="2.1" fill="#101113" />
        <circle cx="14.5" cy="8.5" r="2.1" fill="#2563eb" />
      </svg>
      <span className="text-[15px] font-medium tracking-[-0.01em]">
        Waypoint
      </span>
    </span>
  );
}

const NAV = [
  { href: "#problem", label: "The problem" },
  { href: "#how-it-works", label: "How it works" },
  { href: "#features", label: "Features" },
  { href: "#technician-app", label: "Technician app" },
  { href: "#benchmark", label: "Benchmark" },
];

export default function SiteHeader() {
  return (
    <header className="sticky top-0 z-50 border-b border-line/70 bg-paper/80 backdrop-blur-md">
      <Container className="flex h-14 items-center justify-between gap-4">
        <a href="#top" className="flex items-center" aria-label="Waypoint, home">
          <Logotype />
        </a>

        <nav aria-label="Sections" className="hidden md:block">
          <ul className="flex items-center gap-7">
            {NAV.map((item) => (
              <li key={item.href}>
                <a
                  href={item.href}
                  className="text-[13px] text-ink-2 transition-colors hover:text-ink"
                >
                  {item.label}
                </a>
              </li>
            ))}
          </ul>
        </nav>

        <a
          href="#book-a-demo"
          className="inline-flex items-center rounded-md border border-line-strong bg-surface px-3 py-1.5 text-[13px] font-medium text-ink transition-colors hover:border-ink-2"
        >
          Book a demo
        </a>
      </Container>
    </header>
  );
}
