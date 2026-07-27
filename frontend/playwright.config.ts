import { defineConfig, devices } from "@playwright/test";

/**
 * Playwright E2E config for the Red Team Dashboard frontend.
 *
 * The dashboard has no browser-level tests today. This is the E2E layer of
 * the test platform: it drives real user journeys against a running stack.
 *
 * Two run modes:
 *   - Against an already-running stack:  RTD_E2E_BASE_URL=http://localhost:3000 npx playwright test
 *   - Let Playwright boot the dev server:  npx playwright test   (uses webServer below)
 *
 * Auth: production uses Entra SSO, which can't run headless. Tests that need
 * an authenticated session should gate on `process.env.RTD_E2E_AUTHED === "1"`
 * (a future harness can inject a dev bearer / seeded session). The baseline
 * smoke test only asserts the public shell renders.
 *
 * Browsers are limited to chromium by default to keep CI cheap; add projects
 * for firefox/webkit when cross-browser coverage matters.
 */
const port = Number(process.env.PORT ?? 3100);
const baseURL = process.env.RTD_E2E_BASE_URL ?? `http://localhost:${port}`;

export default defineConfig({
  testDir: "./tests/e2e",
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 2 : 0,
  workers: process.env.CI ? 1 : undefined,
  reporter: process.env.CI ? [["github"], ["html", { open: "never" }]] : "list",
  timeout: 30_000,
  expect: { timeout: 5_000 },
  use: {
    baseURL,
    trace: "on-first-retry",
    screenshot: "only-on-failure",
  },
  projects: [
    { name: "chromium", use: { ...devices["Desktop Chrome"] } },
  ],
  webServer: process.env.RTD_E2E_BASE_URL
    ? undefined
    : {
        command: "npm run dev",
        url: baseURL,
        timeout: 120_000,
        reuseExistingServer: !process.env.CI,
        env: { PORT: String(port) },
      },
});
