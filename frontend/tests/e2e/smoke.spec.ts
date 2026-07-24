import { expect, test } from "@playwright/test";

/**
 * Baseline smoke test: the app boots and renders its shell.
 *
 * This is deliberately minimal — it proves the Playwright harness can reach a
 * running frontend and that the Next.js shell didn't regress to a blank page
 * or a hard crash. Richer journey tests (engagement CRUD, playbook kickoff,
 * findings import) belong in their own spec files and should gate on a seeded
 * backend + an authed session (see docs/TESTING.md).
 */
test.describe("app shell", () => {
  test("landing page renders without a hard error", async ({ page }) => {
    await page.goto("/");
    // The shell always renders the product name in the top bar; a build or
    // auth-config regression turns this into a blank/error page instead.
    await expect(page.locator("body")).not.toBeEmpty();
  });

  test("engagements route is reachable (may redirect to login)", async ({
    page,
  }) => {
    const response = await page.goto("/engagements");
    // Either the page renders (200) or auth redirects (3xx). A 500 here would
    // indicate a real regression rather than an auth gate.
    expect(response?.status()).toBeLessThan(500);
  });
});
