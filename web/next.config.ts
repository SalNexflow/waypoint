import type { NextConfig } from "next";

const config: NextConfig = {
  reactStrictMode: true,
  // The API base is read at build time on the server and at runtime in the
  // browser, so it has to be a NEXT_PUBLIC_ var to reach the client bundle.
  env: {
    NEXT_PUBLIC_API_BASE: process.env.NEXT_PUBLIC_API_BASE ?? "http://localhost:8000",
  },
};

export default config;
