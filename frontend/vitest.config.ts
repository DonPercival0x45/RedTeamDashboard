import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";
import { fileURLToPath } from "node:url";
import path from "node:path";

const here = path.dirname(fileURLToPath(import.meta.url));

// Vitest configuration for the Red Team Dashboard frontend.
//
// The frontend ships ZERO unit/component tests today; this file is the
// foundation of the test platform. It reuses the app's `@/*` path alias
// (tsconfig.json paths) so tests import the same way production code does,
// and runs in jsdom so @testing-library/react + user-event work.
//
// Run:
//   npm test            # watch
//   npm run test:run    # one-shot (CI)
//   npm run test:coverage
//
// Test files live under `test/` (see `include`) and are excluded from the
// Next.js production build via the matching `exclude` in tsconfig + this
// config's build graph.
export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      "@": path.resolve(here),
    },
  },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./vitest.setup.ts"],
    include: ["test/**/*.{test,spec}.{ts,tsx}"],
    exclude: ["node_modules", ".next"],
    css: false,
    coverage: {
      provider: "v8",
      reporter: ["text", "html"],
      include: ["lib/**/*.{ts,tsx}", "components/**/*.{ts,tsx}", "app/**/*.{ts,tsx}"],
      exclude: ["**/*.d.ts", "test/**"],
    },
  },
});
