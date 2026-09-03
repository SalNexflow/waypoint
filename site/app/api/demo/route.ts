import { NextResponse } from "next/server";

/**
 * Demo requests.
 *
 * There is no CRM wired up yet, so this validates the submission and forwards
 * it to whatever `DEMO_WEBHOOK_URL` points at (a Slack incoming webhook, an
 * n8n endpoint, a form service). With no webhook configured it logs the lead
 * to the server output and still returns success — a marketing page that fails
 * on submit because an env var is missing is worse than one that records the
 * lead somewhere a human can retrieve it.
 */

const FLEET_SIZES = new Set([
  "1–5 technicians",
  "6–15 technicians",
  "16–40 technicians",
  "More than 40 technicians",
]);

const MAX_LEN = 200;

function bad(error: string) {
  return NextResponse.json({ error }, { status: 400 });
}

export async function POST(request: Request) {
  let body: unknown;
  try {
    body = await request.json();
  } catch {
    return bad("Could not read that submission.");
  }

  if (typeof body !== "object" || body === null) {
    return bad("Could not read that submission.");
  }

  const { name, company, email, fleetSize } = body as Record<string, unknown>;

  const strings = { name, company, email, fleetSize };
  for (const [key, value] of Object.entries(strings)) {
    if (typeof value !== "string" || value.trim() === "") {
      return bad(`Please fill in the ${key === "fleetSize" ? "fleet size" : key} field.`);
    }
    if (value.length > MAX_LEN) {
      return bad("That submission was longer than we can accept.");
    }
  }

  const lead = {
    name: (name as string).trim(),
    company: (company as string).trim(),
    email: (email as string).trim(),
    fleetSize: (fleetSize as string).trim(),
    receivedAt: new Date().toISOString(),
  };

  // Deliberately loose: enough to catch a typo, not enough to reject a valid
  // address that happens to look unusual.
  if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(lead.email)) {
    return bad("That email address does not look right.");
  }

  if (!FLEET_SIZES.has(lead.fleetSize)) {
    return bad("Please choose a fleet size from the list.");
  }

  const webhook = process.env.DEMO_WEBHOOK_URL;
  if (webhook) {
    try {
      const res = await fetch(webhook, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          text: `Demo request — ${lead.name}, ${lead.company} (${lead.email}), ${lead.fleetSize}`,
          lead,
        }),
      });
      if (!res.ok) {
        console.error("demo webhook rejected the lead", res.status, lead);
        return NextResponse.json(
          { error: "We could not record that just now." },
          { status: 502 },
        );
      }
    } catch (err) {
      console.error("demo webhook unreachable", err, lead);
      return NextResponse.json(
        { error: "We could not record that just now." },
        { status: 502 },
      );
    }
  } else {
    console.info("demo request (no DEMO_WEBHOOK_URL configured)", lead);
  }

  return NextResponse.json({ ok: true });
}
