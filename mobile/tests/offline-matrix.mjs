/**
 * The offline test matrix.
 *
 * Every scenario the spec's "Airplane mode" clause implies, driven against a
 * PRODUCTION build with the service worker active -- because offline
 * navigation is the worker's job and it is off in development.
 *
 *   cd mobile && npm run build && npm start
 *   node tests/offline-matrix.mjs
 *
 * Not a vitest suite. It needs a real browser, a real service worker and a
 * real API, which is three things vitest is deliberately not. The unit tests
 * cover the queue's logic; this covers whether the thing works with the radio
 * off, and the two are answering different questions.
 *
 * Playwright is resolved from web/node_modules, where it was already
 * installed for ad-hoc UI checks, rather than added as a second copy here.
 */

// Playwright is resolved from web/node_modules, where it was already
// installed for ad-hoc UI checks. A second copy here would mean a second
// browser download for the same two scripts.
const { chromium, devices } = await import(
  new URL("../../web/node_modules/playwright/index.mjs", import.meta.url).href
);

const CONSOLE = process.env.WAYPOINT_WEB ?? "http://localhost:3000";
const APP = process.env.WAYPOINT_MOBILE ?? "http://localhost:3002";
const DISPATCH_TOKEN = process.env.WAYPOINT_DISPATCH_TOKEN ?? "";

const results = [];
function record(name, pass, detail = "") {
  results.push({ name, pass, detail });
  console.log(`  ${pass ? "PASS" : "FAIL"}  ${name}${detail ? ` — ${detail}` : ""}`);
}

const browser = await chromium.launch();

/** Issue an access code through the console, as a dispatcher would. */
async function issueCode(technicianName) {
  const ctx = await browser.newContext({ viewport: { width: 1280, height: 800 } });
  const page = await ctx.newPage();
  if (DISPATCH_TOKEN) {
    await page.goto(CONSOLE, { waitUntil: "domcontentloaded" });
    await page.evaluate(
      (t) => localStorage.setItem("waypoint.dispatch.token", t),
      DISPATCH_TOKEN,
    );
  }
  await page.goto(`${CONSOLE}/access`, { waitUntil: "networkidle" });

  // A locked console looks like a hung selector otherwise: the unlock screen
  // has no technician rows, so the wait below just times out after 30s with
  // nothing useful to say.
  if (await page.locator("input[type=password]").count()) {
    throw new Error(
      "The dispatcher console is locked. Set WAYPOINT_DISPATCH_TOKEN to the " +
        "same value as DISPATCH_TOKEN and run again.",
    );
  }

  await page
    .locator("tr", { hasText: technicianName })
    .getByRole("button", { name: /code/i })
    .click();
  await page.waitForSelector(".codepanel-code");
  const code = (await page.locator(".codepanel-code").innerText()).trim();
  await ctx.close();
  return code;
}

async function signedIn(technicianName) {
  const ctx = await browser.newContext(devices["Pixel 7"]);
  const page = await ctx.newPage();
  const errors = [];
  page.on("pageerror", (e) => errors.push(String(e)));

  await page.goto(`${APP}/login`, { waitUntil: "networkidle" });
  await page.locator("#code").fill(await issueCode(technicianName));
  await page.getByRole("button", { name: "Sign in" }).click();
  await page.waitForURL(`${APP}/`);
  await page.waitForTimeout(1500);
  // The worker takes control on the next load; give it one.
  await page.reload({ waitUntil: "networkidle" });
  await page.waitForTimeout(1500);
  return { ctx, page, errors };
}

const offline = async (ctx, page, off) => {
  await ctx.setOffline(off);
  await page.evaluate((o) => window.dispatchEvent(new Event(o ? "offline" : "online")), off);
  await page.waitForTimeout(400);
};

console.log("\n=== OFFLINE MATRIX ===\n");

const { ctx, page, errors } = await signedIn("Ahmad Faizal");

record(
  "service worker controls the page",
  await page.evaluate(() => !!navigator.serviceWorker.controller),
);

const day = await page.evaluate(() =>
  JSON.parse(localStorage.getItem("waypoint.field.day") ?? "null"),
);
record("the day is cached for offline use", !!day?.day?.jobs?.length,
  `${day?.day?.jobs?.length ?? 0} jobs`);

const jobId = day.day.jobs[0].id;

// --- with the radio off ----------------------------------------------------
await offline(ctx, page, true);

await page.goto(`${APP}/`, { waitUntil: "domcontentloaded" });
await page.waitForTimeout(1200);
record(
  "Today opens offline from cache",
  (await page.locator("h2").count()) > 0 || (await page.locator("header").count()) > 0,
  (await page.locator("header").innerText().catch(() => "")).replace(/\n/g, " · "),
);

const strip = await page.locator("main > div").first().innerText().catch(() => "");
record("staleness is shown while offline", /Offline/.test(strip), strip.split("\n")[0]);

await page.goto(`${APP}/job?id=${jobId}`, { waitUntil: "domcontentloaded" });
await page.waitForTimeout(1200);
const addr = await page.locator("main p").nth(1).innerText().catch(() => "");
record("job detail deep-links offline", addr.length > 5, addr);

const navHref = await page
  .locator("a", { hasText: "Navigate" })
  .getAttribute("href")
  .catch(() => "");
record("navigate handoff works offline", /^geo:|^https:\/\/maps\.apple/.test(navHref), navHref);

for (const label of ["On my way", "Arrived"]) {
  await page.getByRole("button", { name: label }).click();
  await page.waitForTimeout(5600);
}
const status = (await page.locator("main p").first().innerText()).trim();
record("status transitions work offline", /ON SITE/.test(status), status);

await page.getByRole("button", { name: "Complete" }).click();
await page.waitForURL(/\/complete\?id=/);
await page.waitForTimeout(900);
record("complete form opens offline", (await page.locator("textarea").count()) === 1);

await page.locator("textarea").fill("Done in a basement.");
await page.getByRole("button", { name: "Done" }).click();
await page.waitForURL(`${APP}/`);
await page.waitForTimeout(1500);

const queued = await page.locator("main > div").first().innerText();
record("everything queued, nothing lost", /waiting to send/.test(queued), queued.split("\n")[0]);
record(
  "sign out refused while unsent",
  await page.getByRole("button", { name: "Sign out" }).isDisabled(),
);

await page.reload({ waitUntil: "domcontentloaded" });
await page.waitForTimeout(1500);
const afterReload = await page.locator("main > div").first().innerText();
record(
  "queue survives an app restart",
  /waiting to send/.test(afterReload),
  afterReload.split("\n")[0],
);
record("done group reflects local work", /\d+ done/.test(await page.locator("main").innerText()));

// --- back on ---------------------------------------------------------------
await offline(ctx, page, false);
await page.waitForTimeout(8000);

const settled = await page.locator("main > div").first().innerText().catch(() => "");
record("queue drains on reconnect", !/waiting to send/.test(settled), settled.split("\n")[0] || "(silent)");
record(
  "sign out allowed once sent",
  !(await page.getByRole("button", { name: "Sign out" }).isDisabled()),
);

// --- no duplicates ---------------------------------------------------------
const counted = await page.evaluate(async ([api, id]) => {
  const token = localStorage.getItem("waypoint.field.token");
  const r = await fetch(`${api}/field/today`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  const d = await r.json();
  return d.jobs.find((j) => j.id === Number(id)) ?? null;
}, [process.env.NEXT_PUBLIC_API_BASE ?? "http://localhost:8000", jobId]);
record(
  "the server agrees the job is done",
  counted?.status === "complete" && counted?.completed === true,
  `status=${counted?.status} completed=${counted?.completed}`,
);

// --- a different technician on the same handset -----------------------------
await page.getByRole("button", { name: "Sign out" }).click();
await page.waitForURL(/\/login/);
const leaked = await page.evaluate(() => localStorage.getItem("waypoint.field.day"));
record("cached day is dropped on sign out", leaked === null);

record("no page errors throughout", errors.length === 0, errors.join("; "));

await browser.close();

const failed = results.filter((r) => !r.pass);
console.log(
  `\n${results.length - failed.length}/${results.length} passed` +
    (failed.length ? `\nFAILED: ${failed.map((f) => f.name).join(", ")}` : ""),
);
process.exit(failed.length ? 1 : 0);
