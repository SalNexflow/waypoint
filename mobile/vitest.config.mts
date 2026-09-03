import { defineConfig } from "vitest/config";
import { fileURLToPath } from "node:url";

export default defineConfig({
  test: {
    // Node, not a DOM environment. What needs testing here is the queue --
    // durability, ordering, serialisation, retry classification -- and none
    // of that touches the DOM. `tests/setup.ts` supplies the two browser
    // globals the queue does use, which is cheaper and more honest than
    // pulling in a whole simulated document.
    environment: "node",
    setupFiles: ["./tests/setup.ts"],
    include: ["tests/**/*.test.ts"],
  },
  resolve: {
    // Same "@/..." alias the app uses, so tests import exactly what ships.
    alias: {
      "@": fileURLToPath(new URL(".", import.meta.url)),
    },
  },
});
