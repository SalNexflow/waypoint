import type { NextConfig } from "next";

const config: NextConfig = {
  reactStrictMode: true,
  env: {
    // Read in the browser, so it must be the host-visible API URL. On a real
    // phone this is NOT localhost -- it is the dev machine's LAN address, and
    // api/main.py's CORS allow_origins has to learn about it before phase 10.
    NEXT_PUBLIC_API_BASE:
      process.env.NEXT_PUBLIC_API_BASE ?? "http://localhost:8000",
    // Service worker registration is off in development by default: a caching
    // SW and Next's hot reload fight, and the SW wins in the most confusing
    // way possible. Set to "1" to test installability without a prod build.
    NEXT_PUBLIC_ENABLE_SW: process.env.NEXT_PUBLIC_ENABLE_SW ?? "",
    // A reusable sign-in code, shown on the login screen so a public demo is
    // not a locked door. Must match DEMO_ACCESS_CODE on the API, and must be
    // empty for any build a real technician will use.
    NEXT_PUBLIC_DEMO_CODE: process.env.NEXT_PUBLIC_DEMO_CODE ?? "",
  },
};

export default config;
