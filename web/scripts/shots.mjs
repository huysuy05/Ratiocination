#!/usr/bin/env node
/**
 * Screenshots of the running app (UI.md §15).
 *
 *   npm run shots -- --base http://127.0.0.1:8765 [options]
 *
 * Opens each route with Playwright Chromium for every theme × viewport (desktop 1440×900, mobile
 * 390×844) against a running backend that serves the built app (with a demo or live database), and
 * writes PNGs to web/test-results/shots/ as `<route>--<viewport>-<theme>.png`.
 *
 * Options (lists accept commas and/or several values):
 *   --base <url>          required; the server (backend serving web/dist, or Vite with a proxy)
 *   --routes <r…>         default: the main routes with --project/--paper ids
 *   --themes <t…>         dark,light (default both)
 *   --viewports <v…>      desktop,mobile (default both)
 *   --project <id>        project id for the default routes (default 1)
 *   --paper <id>          paper id for the default routes (default 1)
 *   --wait <ms>           settle time after load (default 1200)
 *   --full-page           capture the full scrollable page
 *   --out <dir>           output directory (default test-results/shots, relative to web/)
 *   --clean               delete existing PNGs in the output directory first
 */
import { mkdir, readdir, rm } from "node:fs/promises";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { chromium } from "@playwright/test";

const WEB_DIR = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const VIEWPORTS = {
  desktop: { viewport: { width: 1440, height: 900 }, deviceScaleFactor: 1, isMobile: false, hasTouch: false },
  mobile: { viewport: { width: 390, height: 844 }, deviceScaleFactor: 2, isMobile: true, hasTouch: true },
};

function usage(message) {
  if (message) console.error(`shots: ${message}`);
  console.error("usage: npm run shots -- --base <url> [--routes /projects,/dashboard] [--themes dark,light] [--viewports desktop,mobile]");
  process.exit(2);
}

function parseArgs(argv) {
  const options = {};
  let current = null;
  for (const arg of argv) {
    if (arg.startsWith("--")) {
      const [name, inline] = arg.slice(2).split(/=(.*)/s);
      current = name;
      options[name] ??= [];
      if (inline !== undefined) options[name].push(inline);
    } else if (current) {
      options[current].push(arg);
    } else {
      usage(`unexpected argument "${arg}"`);
    }
  }
  return options;
}

const list = (values) => (values ?? []).flatMap((v) => v.split(",")).map((v) => v.trim()).filter(Boolean);
const single = (values, fallback) => list(values).at(-1) ?? fallback;

function defaultRoutes(project, paper) {
  return [
    "/projects",
    "/projects/new",
    `/projects/${project}/feed`,
    `/projects/${project}/feed?paper=${paper}`,
    `/projects/${project}/library`,
    `/projects/${project}/graph`,
    `/projects/${project}/graph/${paper}`,
    `/projects/${project}/documents`,
    `/projects/${project}/settings`,
    `/papers/${paper}?project=${project}`,
    "/dashboard",
    "/settings",
    "/signin",
  ];
}

function fileStem(route) {
  const stem = route.replace(/^\//, "").replace(/[/?=&]+/g, "_").replace(/[^A-Za-z0-9_.-]/g, "");
  return stem || "root";
}

const options = parseArgs(process.argv.slice(2));
const base = single(options.base, null);
if (!base) usage("--base is required");
const project = single(options.project, "1");
const paper = single(options.paper, "1");
const routes = list(options.routes).length > 0 ? list(options.routes) : defaultRoutes(project, paper);
const themes = list(options.themes).length > 0 ? list(options.themes) : ["dark", "light"];
const viewports = list(options.viewports).length > 0 ? list(options.viewports) : ["desktop", "mobile"];
const wait = Number(single(options.wait, "1200"));
const fullPage = options["full-page"] !== undefined;
const out = resolve(WEB_DIR, single(options.out, "test-results/shots"));

await mkdir(out, { recursive: true });
if (options.clean !== undefined) {
  for (const name of await readdir(out)) if (name.endsWith(".png")) await rm(join(out, name));
}

const browser = await chromium.launch();
const written = [];
let errors = 0;
try {
  for (const viewportName of viewports) {
    const viewport = VIEWPORTS[viewportName];
    if (!viewport) usage(`unknown viewport "${viewportName}"`);
    for (const theme of themes) {
      const context = await browser.newContext({ ...viewport, colorScheme: theme === "light" ? "light" : "dark" });
      await context.addInitScript((value) => {
        try {
          localStorage.setItem("rr.theme", value);
        } catch {
          // storage blocked: the default theme applies
        }
      }, theme);
      for (const route of routes) {
        const page = await context.newPage();
        page.on("pageerror", (error) => {
          errors += 1;
          console.error(`  page error on ${route}: ${error.message}`);
        });
        await page.goto(new URL(route, base).toString(), { waitUntil: "networkidle" });
        await page.waitForTimeout(wait);
        const file = join(out, `${fileStem(route)}--${viewportName}-${theme}.png`);
        await page.screenshot({ path: file, fullPage });
        written.push(file);
        await page.close();
      }
      await context.close();
    }
  }
} finally {
  await browser.close();
}
for (const file of written) console.log(file);
if (errors > 0) {
  console.error(`shots: ${errors} page error(s)`);
  process.exitCode = 1;
}
