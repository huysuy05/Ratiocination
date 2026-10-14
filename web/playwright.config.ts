import { mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { defineConfig, devices } from "@playwright/test";

/**
 * End-to-end checks against the real stack: the backend in single-user mode, offline, on a fresh
 * temporary data directory, serving the production build in web/dist. `npm run e2e` builds first.
 * Port: PW_PORT, default 5199.
 */
const port = Number(process.env.PW_PORT ?? 5199);
const baseURL = `http://127.0.0.1:${port}`;
const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
// Only the main process starts the server (workers reload this file but must not make data dirs).
const dataDir = process.env.TEST_WORKER_INDEX === undefined ? mkdtempSync(join(tmpdir(), "radar-e2e-")) : "";

function inheritedEnv(): Record<string, string> {
  const env: Record<string, string> = {};
  for (const [key, value] of Object.entries(process.env)) {
    if (value !== undefined) env[key] = value;
  }
  return env;
}

export default defineConfig({
  testDir: "./e2e",
  outputDir: "./test-results/e2e",
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: [["list"]],
  use: { baseURL, trace: "retain-on-failure" },
  webServer: {
    command: `${root}/.venv/bin/uvicorn radar.main:create_app --factory --host 127.0.0.1 --port ${port} --workers 1`,
    cwd: join(root, "backend"),
    url: `${baseURL}/api/health`,
    reuseExistingServer: false,
    timeout: 60_000,
    env: {
      ...inheritedEnv(),
      RADAR_AUTH: "local",
      RADAR_DATA_DIR: dataDir,
      RADAR_DATABASE_URL: "",
      RADAR_OFFLINE: "1",
      RADAR_SCHEDULER: "0",
      RADAR_ENV_FILE: "",
    },
  },
  projects: [
    {
      name: "desktop",
      use: { ...devices["Desktop Chrome"], viewport: { width: 1440, height: 900 } },
    },
    {
      name: "mobile",
      use: {
        ...devices["Desktop Chrome"],
        viewport: { width: 390, height: 844 },
        deviceScaleFactor: 3,
        isMobile: true,
        hasTouch: true,
      },
    },
  ],
});
