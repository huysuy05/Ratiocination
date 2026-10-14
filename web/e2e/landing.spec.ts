import { expect, test, type Page } from "@playwright/test";

/**
 * The public landing page on the real stack. The e2e server runs in local mode, where `/` goes
 * straight into the app (smoke.spec.ts); `/welcome` shows the landing page to everyone.
 */

function collectErrors(page: Page): string[] {
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  page.on("console", (message) => {
    if (message.type() === "error") errors.push(message.text());
  });
  page.on("response", (response) => {
    if (response.status() >= 400) errors.push(`HTTP ${response.status()} ${response.request().method()} ${new URL(response.url()).pathname}`);
  });
  return errors;
}

test("/welcome renders the landing page with working links and no horizontal scroll", async ({ page }) => {
  const errors = collectErrors(page);
  await page.goto("/welcome");
  await expect(page.getByRole("heading", { level: 1, name: /^Ratiocination/ })).toBeVisible();
  const nav = page.getByRole("navigation", { name: "Main" });
  await expect(nav.getByRole("link", { name: "Sign in" })).toHaveAttribute("href", "/signin");
  await expect(nav.getByRole("link", { name: "Sign in" })).toBeVisible();
  await expect(page.getByRole("link", { name: "Create an account" })).toHaveAttribute("href", "/signup");
  await expect(page.getByRole("heading", { level: 2, name: "How it works" })).toBeAttached();
  // The portrait loop picks the WebM source and starts muted.
  await expect.poll(() => page.evaluate(() => document.querySelector("video")?.currentSrc ?? "")).toMatch(/ratiocination-loop\.webm$/);
  await expect.poll(() => page.evaluate(() => document.querySelector("video")?.muted)).toBe(true);
  // Scroll through every section (reveals, draw-ins), then check the layout still fits.
  const height = await page.evaluate(() => document.documentElement.scrollHeight);
  for (let y = 0; y < height; y += 600) await page.evaluate((top) => window.scrollTo(0, top), y);
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
  expect(overflow).toBeLessThanOrEqual(0);
  await expect(page.getByRole("contentinfo").getByRole("link", { name: "Source code (AGPL)" })).toBeVisible();
  expect(errors).toEqual([]);
});

test("in-page anchors jump to the feature rows", async ({ page }) => {
  await page.goto("/welcome");
  await page.getByRole("link", { name: "How it works" }).first().click();
  await expect(page).toHaveURL(/\/welcome#how-it-works$/);
  await expect(page.getByRole("heading", { level: 2, name: "How it works" })).toBeInViewport();
});

test("reduced motion shows the still portrait instead of the video", async ({ page }) => {
  const errors = collectErrors(page);
  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.goto("/welcome");
  await expect(page.getByRole("heading", { level: 1, name: /^Ratiocination/ })).toBeVisible();
  await expect(page.locator("html")).toHaveAttribute("data-motion", "reduced");
  await expect(page.locator("video")).toHaveCount(0);
  await expect(page.locator('img[src="/landing/ratiocination-loop-poster.jpg"]')).toBeVisible();
  await expect(page.getByRole("button", { name: /moving categories/ })).toHaveCount(0);
  expect(errors).toEqual([]);
});
