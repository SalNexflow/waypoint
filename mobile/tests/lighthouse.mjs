/**
 * Lighthouse, plus the installability checks Lighthouse no longer does.
 *
 *   cd mobile && npm run build && npm start
 *   node tests/lighthouse.mjs
 *
 * WHY THIS IS TWO THINGS
 * ----------------------
 * The spec asks for "Lighthouse PWA audit passes". That audit no longer
 * exists: Lighthouse 12 REMOVED the PWA category in 2024, and the current
 * categories are performance, accessibility, best-practices and seo. There is
 * nothing to make pass.
 *
 * So this runs the categories that do exist -- accessibility and
 * best-practices being the ones that matter for a screen read at arm's length
 * in sunlight -- and then checks installability directly against the criteria
 * Chrome actually applies. That is a closer reading of what the spec wanted
 * than reporting a score for an audit that was deleted.
 *
 * MUST be run against a production build over http://localhost. Service
 * workers require a secure context, and localhost counts as one while
 * http://192.168.x.x does not -- which is the single most important fact for
 * device testing.
 */

import fs from "node:fs";
import lighthouse from "lighthouse";
// Resolved from web/node_modules -- see tests/offline-matrix.mjs.
const { chromium } = await import(
  new URL("../../web/node_modules/playwright/index.mjs", import.meta.url).href
);

// Not named `URL`: that shadows the global constructor used at the bottom.
const TARGET = process.env.WAYPOINT_MOBILE ?? "http://localhost:3002";
const PORT = 9222;

const browser = await chromium.launch({
  args: [`--remote-debugging-port=${PORT}`],
});

// --- Installability, checked by hand ---------------------------------------

const page = await browser.newPage();
await page.goto(`${TARGET}/login`, { waitUntil: "networkidle" });
await page.waitForTimeout(1500);
await page.reload({ waitUntil: "networkidle" }); // let the worker take control
await page.waitForTimeout(1500);

const cdp = await page.context().newCDPSession(page);
const { data: manifestRaw, errors: manifestErrors } =
  await cdp.send("Page.getAppManifest");
const manifest = manifestRaw ? JSON.parse(manifestRaw) : null;

const sw = await page.evaluate(async () => {
  const reg = await navigator.serviceWorker.getRegistration();
  return {
    registered: !!reg,
    active: !!reg?.active,
    controlling: !!navigator.serviceWorker.controller,
    scope: reg?.scope ?? null,
  };
});

const icons = manifest?.icons ?? [];
const sizes = new Set(icons.flatMap((i) => (i.sizes ?? "").split(" ")));

const checks = [
  ["secure context (required for service workers)",
   await page.evaluate(() => window.isSecureContext)],
  ["manifest parses with no errors",
   !!manifest && manifestErrors.filter((e) => e.critical).length === 0,
   manifestErrors.map((e) => e.message).join("; ")],
  ["manifest has name and short_name", !!manifest?.name && !!manifest?.short_name,
   `${manifest?.name} / ${manifest?.short_name}`],
  ["display is standalone", manifest?.display === "standalone", manifest?.display],
  ["start_url is set", !!manifest?.start_url, manifest?.start_url],
  ["a 192px icon", sizes.has("192x192")],
  ["a 512px icon", sizes.has("512x512")],
  ["a maskable icon", icons.some((i) => (i.purpose ?? "").includes("maskable"))],
  ["service worker is registered and active", sw.registered && sw.active, sw.scope],
  ["service worker controls the page", sw.controlling],
];

// The one criterion that cannot be read off the page: does the shell survive
// with the radio off. Asked directly rather than inferred from the manifest.
await page.context().setOffline(true);
let offlineOk = false;
try {
  const res = await page.goto(`${TARGET}/`, { waitUntil: "domcontentloaded" });
  offlineOk = !!res && res.status() < 400;
} catch {
  offlineOk = false;
}
await page.context().setOffline(false);
checks.push(["start_url responds with the radio off", offlineOk]);

console.log("\n=== INSTALLABILITY ===");
console.log("(Lighthouse 12 removed the PWA category; these are the criteria it used to check.)\n");
let failed = 0;
for (const [name, ok, detail] of checks) {
  if (!ok) failed += 1;
  console.log(`  ${ok ? "PASS" : "FAIL"}  ${name}${detail ? ` — ${detail}` : ""}`);
}

// --- Lighthouse, for the categories that still exist ------------------------

await page.close();

const report = await lighthouse(
  `${TARGET}/login`,
  { port: PORT, output: "json", logLevel: "error", formFactor: "mobile", screenEmulation: { mobile: true, width: 412, height: 915, deviceScaleFactor: 2.6, disabled: false } },
);

console.log("\n=== LIGHTHOUSE (mobile) ===\n");
const scores = {};
for (const [key, cat] of Object.entries(report.lhr.categories)) {
  scores[key] = Math.round((cat.score ?? 0) * 100);
  console.log(`  ${String(scores[key]).padStart(3)}  ${cat.title}`);
}

const notable = Object.values(report.lhr.audits).filter(
  (a) => a.score !== null && a.score < 0.9 && a.scoreDisplayMode === "binary",
);
if (notable.length) {
  console.log("\n  Failing audits:");
  for (const a of notable) console.log(`    - ${a.title}`);
}

fs.writeFileSync(
  new URL("../lighthouse-report.json", import.meta.url),
  JSON.stringify({ scores, installability: checks.map(([n, ok]) => ({ n, ok })) }, null, 2),
);

await browser.close();
process.exit(failed ? 1 : 0);
