// @vitest-environment node
/**
 * Node-environment test (`*.node.test.ts`: type-checked by tsconfig.node.json, not the app config).
 * UI.md §12 guard: the production bundle never contains mock data. `npm run build` also runs
 * scripts/check-dist.mjs; this test re-checks whatever `dist/` exists (skipped before a build).
 */
import { existsSync, readdirSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

const DIST = join(process.cwd(), "dist");

function listFiles(dir: string): string[] {
  return readdirSync(dir).flatMap((name) => {
    const path = join(dir, name);
    return statSync(path).isDirectory() ? listFiles(path) : [path];
  });
}

describe("production bundle", () => {
  it.skipIf(!existsSync(DIST))("contains no mock data", () => {
    const offenders = listFiles(DIST).filter((file) => readFileSync(file).includes("Sample paper"));
    expect(offenders).toEqual([]);
  });

  it.skipIf(!existsSync(DIST))("does not ship the mock module", () => {
    const offenders = listFiles(DIST).filter((file) => readFileSync(file).includes("API mock mode"));
    expect(offenders).toEqual([]);
  });
});
