import { expect, test, type Page } from "@playwright/test";

/** The real stack: backend (single-user, offline, empty database) serving web/dist. */

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

test("boots, redirects / to /projects, applies the theme, and has no horizontal scroll", async ({ page }) => {
  const errors = collectErrors(page);
  await page.goto("/");
  await expect(page).toHaveURL(/\/projects$/);
  await expect(page.getByRole("heading", { level: 1, name: "Projects" })).toBeVisible();
  await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
  await expect(page.locator("html")).toHaveAttribute("data-motion", /^(full|reduced)$/);
  await expect(page.locator('meta[name="referrer"]')).toHaveAttribute("content", "no-referrer");
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
  expect(overflow).toBeLessThanOrEqual(0);
  expect(errors).toEqual([]);
});

test("the stored theme applies before the app renders", async ({ page }) => {
  await page.addInitScript(() => window.localStorage.setItem("rr.theme", "light"));
  await page.goto("/settings");
  await expect(page.locator("html")).toHaveAttribute("data-theme", "light");
  await expect(page.getByRole("heading", { level: 1, name: "Settings" })).toBeVisible();
});

test("the wizard creates a draft project and can discard it", async ({ page }) => {
  const errors = collectErrors(page);
  await page.goto("/projects/new");
  await page.getByLabel(/Project name/).fill("E2E radar");
  await page.getByLabel(/Research question/).fill("Does the wizard create a draft project?");
  await page.getByRole("button", { name: "Next" }).click();
  await page.getByRole("checkbox", { name: /Information Retrieval/ }).click();
  await page.getByRole("button", { name: /Create draft/ }).click();
  await expect(page.getByRole("heading", { name: "Papers you already trust" })).toBeVisible();
  await expect(page).toHaveURL(/\/projects\/new\?project=\d+&step=2$/);
  await page.getByRole("button", { name: "Discard draft" }).click();
  await page.getByRole("alertdialog").getByRole("button", { name: "Discard draft" }).click();
  await expect(page).toHaveURL(/\/projects$/);
  expect(errors).toEqual([]);
});

test("the dashboard renders with no data yet", async ({ page }) => {
  const errors = collectErrors(page);
  await page.goto("/dashboard?days=7");
  await expect(page.getByRole("heading", { level: 1, name: "Dashboard" })).toBeVisible();
  await expect(page.getByText("No swipes in this period yet. Your deck is waiting.")).toBeVisible();
  expect(errors).toEqual([]);
});

test("keywords entered in the wizard are saved with the project and editable in its settings", async ({ page }) => {
  const errors = collectErrors(page);
  await page.goto("/projects/new");
  await page.getByLabel(/Project name/).fill("E2E keywords");
  await page.getByLabel(/Research question/).fill("Does the keyword field save what I type?");
  const keywords = page.getByRole("textbox", { name: "Keywords" });
  await keywords.fill("conformal prediction");
  await keywords.press("Enter");
  await keywords.fill("RAG");
  await keywords.press(",");
  await keywords.fill("rag");
  await keywords.press("Enter");
  await expect(page.getByText("“RAG” is already in the list.")).toBeVisible();
  await keywords.fill("");
  await page.getByRole("button", { name: "Next" }).click();
  await page.getByRole("checkbox", { name: /Information Retrieval/ }).click();
  await page.getByRole("button", { name: /Create draft/ }).click();
  await expect(page.getByRole("heading", { name: "Papers you already trust" })).toBeVisible();
  const projectId = new URL(page.url()).searchParams.get("project");
  expect(projectId).not.toBeNull();

  await page.goto(`/projects/${projectId}/settings`);
  const chips = page.getByRole("list", { name: "Keywords added" }).getByRole("listitem");
  await expect(chips).toHaveText(["conformal prediction", "RAG"]);
  await page.getByRole("button", { name: "Remove keyword “RAG”" }).click();
  await page.getByRole("textbox", { name: "Keywords" }).fill("self-supervised");
  await page.getByRole("textbox", { name: "Keywords" }).press("Enter");
  await page.getByRole("button", { name: "Save basics" }).click();
  await expect(page.getByRole("button", { name: "Save basics" })).toBeDisabled();
  await page.reload();
  await expect(page.getByRole("list", { name: "Keywords added" }).getByRole("listitem")).toHaveText(["conformal prediction", "self-supervised"]);
  expect(errors).toEqual([]);
});
