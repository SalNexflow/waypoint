import {
  DAY_END,
  DAY_START,
  ROUTES,
  ROUTE_COLOURS,
  UNASSIGNED,
} from "./dispatch-data";

/**
 * The dispatch view: a map with one coloured route per technician, a
 * technician panel, and a timeline underneath. Rendered as SVG and DOM rather
 * than shipped as a bitmap so it stays sharp at any width and stays readable
 * on a phone.
 */

const span = DAY_END - DAY_START;
const pct = (minutes: number) => ((minutes - DAY_START) / span) * 100;

function hhmm(minutes: number) {
  const h = Math.floor(minutes / 60);
  const m = minutes % 60;
  return `${String(h).padStart(2, "0")}:${String(m).padStart(2, "0")}`;
}

/** First arrival to last departure, for the technician panel. */
function daySpan(index: number) {
  const jobs = ROUTES[index].jobs;
  const last = jobs[jobs.length - 1];
  return `${hhmm(jobs[0].start)}–${hhmm(last.start + last.dur)}`;
}

/* The basemap is drawn well outside the 1000x430 viewBox: the SVG is scaled to
   cover its container, so at narrow widths the visible slice moves and the
   cartography has to keep going past the edges. */
function Basemap() {
  return (
    <>
      <rect x="-400" y="-300" width="1800" height="1100" fill="#e8eae7" />
      {/* Built-up blocks, kept at very low contrast so the routes carry the
          eye rather than the cartography. */}
      <g fill="#f0f1ee">
        <path d="M-180 30h330v120h-330z" />
        <path d="M60 40h250v120H60z" />
        <path d="M380 20h300v150H380z" />
        <path d="M740 40h250v120H740z" />
        <path d="M1040 60h260v130h-260z" />
        <path d="M-160 210h300v140h-300z" />
        <path d="M190 200h250v130H190z" />
        <path d="M490 215h250v140H490z" />
        <path d="M790 205h270v150H790z" />
        <path d="M1110 240h250v130h-250z" />
        <path d="M40 390h330v120H40z" />
        <path d="M420 400h340v120H420z" />
        <path d="M820 395h300v120H820z" />
      </g>
      {/* Parkland and a river. */}
      <path
        d="M596 66c34-22 78-14 96 16 18 30 4 66-28 78-40 15-84-6-92-40-6-26 6-42 24-54z"
        fill="#dfe7dc"
      />
      <path
        d="M92 312c40-18 74-6 96 18 24 26 8 62-26 72-38 12-78-8-84-38-4-22 2-42 14-52z"
        fill="#dfe7dc"
      />
      <path
        d="M-300 96c180 30 276 90 348 172 74 84 152 148 296 178 120 26 260 24 420 6"
        stroke="#cddce4"
        strokeWidth="9"
        fill="none"
        strokeLinecap="round"
      />
      {/* Minor street grid. */}
      <g stroke="#dcdfda" strokeWidth="1">
        {Array.from({ length: 43 }, (_, i) => (
          <line key={`v${i}`} x1={-400 + i * 42} y1="-300" x2={-400 + i * 42} y2="800" />
        ))}
        {Array.from({ length: 27 }, (_, i) => (
          <line key={`h${i}`} x1="-400" y1={-300 + i * 42} x2="1400" y2={-300 + i * 42} />
        ))}
      </g>
      {/* Arterials and the ring road. */}
      <g stroke="#ffffff" strokeWidth="6" fill="none" strokeLinecap="round">
        <path d="M-400 250C-120 226 160 190 470 182s560 22 930 2" />
        <path d="M520 -300c-18 180-30 330-16 460 10 92 30 140 44 340" />
        <path d="M-400 -20C-100 30 200 120 380 240s240 210 420 300" />
        <path d="M1400 60c-260 40-430 96-560 190s-190 180-280 320" />
        <path d="M-100 800c120-200 260-300 470-330s400-30 640-20" />
        <path d="M-400 470c260-40 520-52 820-40s420 34 980 22" />
      </g>
      <path
        d="M600 60c150 20 232 92 216 190-16 96-130 156-260 148-128-8-210-78-206-174 4-92 104-176 250-164z"
        stroke="#ffffff"
        strokeWidth="8"
        fill="none"
      />
    </>
  );
}

function MapPanel() {
  return (
    <svg
      viewBox="0 0 1000 430"
      preserveAspectRatio="xMidYMid slice"
      className="h-full w-full"
      aria-hidden="true"
      focusable="false"
    >
      <Basemap />

      {ROUTES.map((route, i) => {
        const colour = ROUTE_COLOURS[i % ROUTE_COLOURS.length];
        const points = [route.home, ...route.stops];
        const d = points
          .map((p, j) => `${j === 0 ? "M" : "L"}${p[0]} ${p[1]}`)
          .join(" ");
        return (
          <g key={route.name}>
            {/* A white halo under each line keeps every colour legible where
                routes cross. */}
            <path
              d={d}
              fill="none"
              stroke="#ffffff"
              strokeWidth="6.5"
              strokeLinejoin="round"
              strokeLinecap="round"
              opacity="0.85"
            />
            <path
              d={d}
              fill="none"
              stroke={colour}
              strokeWidth="3"
              strokeLinejoin="round"
              strokeLinecap="round"
            />
            <rect
              x={route.home[0] - 5}
              y={route.home[1] - 5}
              width="10"
              height="10"
              rx="2"
              fill={colour}
              stroke="#ffffff"
              strokeWidth="1.5"
            />
            {route.stops.map((p, j) => (
              <circle
                key={j}
                cx={p[0]}
                cy={p[1]}
                r="5.5"
                fill="#ffffff"
                stroke={colour}
                strokeWidth="3"
              />
            ))}
          </g>
        );
      })}

      {UNASSIGNED.map((u) => (
        <circle
          key={u.reason}
          cx={u.at[0]}
          cy={u.at[1]}
          r="6"
          fill="#ffffff"
          stroke="#b45309"
          strokeWidth="2.5"
          strokeDasharray="3 2.5"
        />
      ))}

    </svg>
  );
}

function TechPanel() {
  return (
    <div className="hidden w-56 shrink-0 flex-col border-l border-line bg-surface lg:flex">
      <div className="border-b border-line px-3 py-1.5 text-[11px] font-medium uppercase tracking-[0.08em] text-muted">
        Technicians
      </div>
      <ul className="divide-y divide-line">
        {ROUTES.map((route, i) => (
          <li key={route.name} className="flex items-center gap-2.5 px-3 py-1">
            <span
              className="size-2.5 shrink-0 rounded-[3px]"
              style={{
                backgroundColor: ROUTE_COLOURS[i % ROUTE_COLOURS.length],
              }}
            />
            <span className="min-w-0">
              <span className="block truncate text-[12px] leading-4 text-ink">
                {route.name}
              </span>
              <span className="tabular block truncate text-[11px] leading-4 text-muted">
                {route.jobs.length} jobs &middot; {daySpan(i)}
              </span>
            </span>
          </li>
        ))}
      </ul>
      <div className="mt-auto border-t border-line px-3 py-1.5">
        <div className="text-[11px] font-medium uppercase tracking-[0.08em] text-muted">
          Held back
        </div>
        <ul className="mt-1.5 space-y-1.5">
          {UNASSIGNED.map((u) => (
            <li key={u.reason} className="text-[11px] leading-4 text-ink-2">
              {u.reason}
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}

function Timeline() {
  const hours = Array.from({ length: 13 }, (_, i) => 7 + i);
  return (
    <div className="border-t border-line bg-surface">
      <div className="flex items-center gap-3 border-b border-line px-3 py-1.5">
        <span className="text-[11px] font-medium uppercase tracking-[0.08em] text-muted">
          Timeline
        </span>
        <span className="tabular text-[11px] text-muted">07:00 &ndash; 19:00</span>
      </div>

      <div className="flex">
        <div className="w-24 shrink-0 sm:w-28" />
        <div className="relative mr-5 h-4 min-w-0 grow">
          {hours.map((h, i) => (
            <span
              key={h}
              className={`tabular absolute top-0 -translate-x-1/2 text-[10px] text-muted${
                i % 2 === 1 ? " max-sm:hidden" : ""
              }`}
              style={{ left: `${pct(h * 60)}%` }}
            >
              {String(h).padStart(2, "0")}
            </span>
          ))}
        </div>
      </div>

      {ROUTES.map((route, i) => {
        const colour = ROUTE_COLOURS[i % ROUTE_COLOURS.length];
        return (
          <div key={route.name} className="flex items-center border-t border-line">
            <div className="flex w-24 shrink-0 items-center gap-2 px-2 py-1 sm:w-28 sm:px-3">
              <span
                className="size-2 shrink-0 rounded-[2px]"
                style={{ backgroundColor: colour }}
              />
              <span className="truncate text-[11px] text-ink-2">{route.name}</span>
            </div>
            <div className="relative mr-5 h-6 min-w-0 grow">
              {/* The shift bar: contracted hours, which jobs must fit inside. */}
              <span
                className="absolute top-1 h-4 rounded-[2px] bg-sunken"
                style={{
                  left: `${pct(8 * 60)}%`,
                  width: `${pct(17 * 60 + 30) - pct(8 * 60)}%`,
                }}
              />
              {route.jobs.map((job) => (
                <span
                  key={job.label}
                  className="absolute top-1 h-4 rounded-[2px]"
                  style={{
                    left: `${pct(job.start)}%`,
                    width: `${(job.dur / span) * 100}%`,
                    backgroundColor: colour,
                  }}
                />
              ))}
            </div>
          </div>
        );
      })}
    </div>
  );
}

export default function DispatchView() {
  return (
    <div
      className="overflow-hidden rounded-lg border border-line-strong bg-surface shadow-[0_1px_2px_rgba(16,17,19,0.04),0_18px_50px_-20px_rgba(16,17,19,0.28)]"
      role="img"
      aria-label="The Waypoint dispatch view: a map of the Klang Valley showing eight coloured technician routes, a panel listing each technician and their jobs, and a timeline placing every job across the working day."
    >
      {/* Application chrome. */}
      <div className="flex flex-wrap items-center gap-x-4 gap-y-1.5 border-b border-line bg-surface px-3 py-2">
        <span className="text-[12px] font-medium text-ink">Dispatch</span>
        <span className="tabular text-[12px] text-muted">Wed 3 Sep</span>
        <dl className="ml-auto flex flex-wrap items-center gap-x-4 gap-y-1 sm:gap-x-6">
          {[
            ["Assigned", "43 / 45"],
            ["Travel", "12.5 min/job"],
            ["Windows met", "42 / 43"],
            ["Solved in", "1.8 s"],
          ].map(([k, v]) => (
            <div key={k} className="leading-tight">
              <dt className="text-[9px] uppercase tracking-[0.07em] text-muted">
                {k}
              </dt>
              <dd className="tabular text-[12px] font-medium text-ink">{v}</dd>
            </div>
          ))}
        </dl>
      </div>

      <div className="flex h-[230px] overflow-hidden sm:h-[300px] lg:h-[430px]">
        <div className="relative min-w-0 grow overflow-hidden bg-[#e8eae7]">
          <MapPanel />
          <span className="absolute bottom-0 right-0 bg-white/75 px-1.5 py-0.5 text-[10px] text-muted">
            &copy; OpenStreetMap contributors
          </span>
        </div>
        <TechPanel />
      </div>

      <Timeline />
    </div>
  );
}
