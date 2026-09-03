import { Logotype } from "./SiteHeader";
import { Container } from "./ui";

const LINKS = [
  { href: "#problem", label: "The problem" },
  { href: "#how-it-works", label: "How it works" },
  { href: "#features", label: "Features" },
  { href: "#technician-app", label: "Technician app" },
  { href: "#benchmark", label: "Benchmark" },
  { href: "#book-a-demo", label: "Book a demo" },
];

export default function SiteFooter() {
  return (
    <footer className="border-t border-line bg-surface py-12">
      <Container>
        <div className="flex flex-col gap-8 sm:flex-row sm:items-start sm:justify-between">
          <div>
            <Logotype />
            <p className="mt-3 max-w-xs text-[13px] leading-relaxed text-muted">
              Constraint-based scheduling and routing for teams that send
              technicians to customer sites.
            </p>
          </div>

          <nav aria-label="Footer">
            <ul className="flex flex-wrap gap-x-6 gap-y-2">
              {LINKS.map((link) => (
                <li key={link.href}>
                  <a
                    href={link.href}
                    className="text-[13px] text-ink-2 transition-colors hover:text-ink"
                  >
                    {link.label}
                  </a>
                </li>
              ))}
            </ul>
          </nav>
        </div>

        <div className="mt-10 flex flex-col gap-2 border-t border-line pt-6 text-[12px] text-muted sm:flex-row sm:items-center sm:justify-between">
          <p>© {new Date().getFullYear()} Waypoint. All rights reserved.</p>
          <p>
            Routing data ©{" "}
            <a
              href="https://www.openstreetmap.org/copyright"
              className="underline decoration-line-strong underline-offset-2 transition-colors hover:text-ink"
              rel="noreferrer"
            >
              OpenStreetMap contributors
            </a>
            .
          </p>
        </div>
      </Container>
    </footer>
  );
}
