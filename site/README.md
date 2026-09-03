# Waypoint marketing site

Single-page Next.js (App Router) + Tailwind v4 marketing site. Separate from
`web/`, which is the product UI — this app has no dependency on the API, the
solver or OSRM, and deploys on its own.

```bash
npm install
npm run dev       # http://localhost:3001
npm run build && npm start
```

## Deploying to Vercel

Import the repository and set **Root Directory** to `site`. Everything else is
the Next.js default: build `next build`, install `npm install`, no output
directory override.

| Environment variable | Required | What it does |
|---|---|---|
| `DEMO_WEBHOOK_URL` | no | Where `POST /api/demo` forwards a demo request (Slack incoming webhook, n8n endpoint, form service). Unset, the route logs the lead to the server output and still returns success, so the form never breaks on a missing variable. |

## Layout

```
app/layout.tsx        fonts, metadata
app/page.tsx          the one page: section order lives here
app/api/demo/route.ts demo-request handler and validation
app/icon.svg          favicon
components/           one file per section, plus ui.tsx primitives
components/DispatchView.tsx   the hero dispatch view
components/dispatch-data.ts   its route geometry and timeline blocks
```

## The hero image

The hero is a rendering of the dispatch view — SVG map, technician panel and
timeline — not a screen capture. It uses the product's own route palette
(`web/lib/api.ts`, `ROUTE_COLOURS`) and Klang Valley job locations projected
into a viewBox, so it shows the same thing the software does, stays sharp at
any width and readable on a phone.

To swap in a real screenshot once there is a day worth capturing: put the PNG
in `public/`, and in `components/Hero.tsx` replace `<DispatchView />` with
`next/image`. Keep the caption underneath.

## The technician-app screenshots

`public/app/*.png` are real captures of the PWA in `mobile/`, taken at 390x844
and 3x from a **production** build on :3002 (`npm run build && npm start` in
`mobile/`, with the `mobile` compose container stopped so the port is free).
The dev container is not usable for this — Next's dev indicator sits in the
corner of every shot.

Two things have to be arranged for the capture, neither of which changes the
app:

- **The day.** The API's "today" is whatever the clock says; the solved day in
  the dev database is not. Rewrite `/field/today` to ask for the solved date,
  and set the browser's clock to that date as well — `lib/day-cache.ts` throws
  away a cached day that is not today on the device, which is what makes the
  offline screens work.
- **Pending changes.** Answer `/field/changes` with `[]` if the technician has
  unacknowledged retimes; acknowledging them for real is a write to the server.

The offline shot queues three status updates with the radio off and the browser
is closed while still offline, so the queue never reaches the API. Note that a
status tap only lands in the outbox after the five-second undo window lapses.

To re-take them, sign in with a code from `POST /technicians/{id}/access-code`
(or `http://localhost:3000/access`), walk On my way -> Arrived -> Complete, and
screenshot Today, a job, the Complete form and Today again while offline.

## Colour

The palette lives in `app/globals.css` as tokens. Four band colours — amber,
sky, violet, rose — each with three strengths:

| token | used for |
|---|---|
| `--color-amber` etc. | flat full-width bands (the "How it works" steps), the rules above the problem cards |
| `--color-amber-tint` etc. | pale plates: highlighted words in copy (`.hl`), the feature icon squares |
| `--color-amber-ink` etc. | the only versions used for text on a light background — all four clear 4.5:1 |
| `--color-amber-bright`, `--color-sky-bright` | the two figures on the dark benchmark panel |

`.wash` is the gradient behind the header/hero and, mirrored, behind the
closing call to action. It is a flat multi-stop gradient that resolves into
paper, not a blob, and it carries the chevron motif in `Chevrons.tsx`.

Text on a band is always ink at 75–100% opacity; nothing relies on a hue for
contrast. Run the axe check after changing any of this — the violet band's
label failed at 65% ink and needed 75%.

## Copy that is load-bearing

The benchmark figures in `components/Proof.tsx` are quoted from the harness
(see the Benchmark section of the root `README.md`) and are stated against the
strongest baseline on each metric. If the benchmark is re-run, update both the
two headline percentages and the table rows together, and keep the caveat line
about 9 instances and no traffic model.

`components/Testimonials.tsx` holds three placeholder slots. The comment above
the array shows the shape to fill in; setting `placeholder: false` on an entry
switches that card from dimmed-and-dashed to a normal card and adds the quote
marks.
