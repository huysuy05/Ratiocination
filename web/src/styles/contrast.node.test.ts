// @vitest-environment node
/**
 * UI.md §11 contrast guard: parses tokens.css and checks WCAG 2.x contrast ratios in both themes.
 * - Text tokens (and --accent, which is used for links) need ≥ 4.5:1 on every surface.
 * - --on-accent needs ≥ 4.5:1 on --accent (primary button labels).
 * - State, edge, disruption and score-breakdown colours are non-text marks and need ≥ 3:1 on every surface.
 */
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

const CSS = readFileSync(join(process.cwd(), "src/styles/tokens.css"), "utf8");

const SURFACES = ["--bg", "--surface", "--surface-2", "--surface-3"] as const;
const TEXT_TOKENS = ["--text", "--text-muted", "--text-faint", "--accent"] as const;
const MARK_TOKENS = [
  "--relevant",
  "--not-relevant",
  "--save",
  "--read",
  "--edge-cites",
  "--edge-similar",
  "--edge-topic",
  "--edge-authored",
  "--disruptive",
  "--consolidating",
  "--feat-question",
  "--feat-anchor",
  "--feat-keyword",
  "--feat-draft",
  "--feat-citation",
  "--feat-preference",
  "--feat-recency",
] as const;

/** Custom properties declared directly inside the first rule whose selector matches `selector`. */
function block(selector: string): Map<string, string> {
  const start = CSS.indexOf(`${selector} {`);
  if (start < 0) throw new Error(`selector ${selector} not found in tokens.css`);
  const end = CSS.indexOf("\n}", start);
  const body = CSS.slice(start, end).replace(/\/\*[\s\S]*?\*\//g, "");
  const vars = new Map<string, string>();
  for (const match of body.matchAll(/(--[a-z0-9-]+)\s*:\s*([^;]+);/g)) {
    vars.set(match[1]!, match[2]!.trim());
  }
  return vars;
}

const LIGHT = block(":root");
const DARK = new Map([...LIGHT, ...block('[data-theme="dark"]')]);

function rgb(hex: string): [number, number, number] {
  const value = hex.replace("#", "");
  const full = value.length === 3 ? [...value].map((c) => c + c).join("") : value;
  if (!/^[0-9a-fA-F]{6}$/.test(full)) throw new Error(`not a hex colour: ${hex}`);
  return [0, 2, 4].map((i) => Number.parseInt(full.slice(i, i + 2), 16)) as [number, number, number];
}

function luminance(hex: string): number {
  const [r, g, b] = rgb(hex).map((channel) => {
    const s = channel / 255;
    return s <= 0.04045 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4;
  }) as [number, number, number];
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

export function contrast(a: string, b: string): number {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x) as [number, number];
  return (hi + 0.05) / (lo + 0.05);
}

function token(theme: Map<string, string>, name: string): string {
  const value = theme.get(name);
  if (value === undefined) throw new Error(`token ${name} is not defined`);
  return value;
}

describe("contrast helper", () => {
  it("matches known WCAG values", () => {
    expect(contrast("#000000", "#ffffff")).toBeCloseTo(21, 5);
    expect(contrast("#777777", "#ffffff")).toBeCloseTo(4.48, 2);
  });
});

for (const [name, theme] of [
  ["light", LIGHT],
  ["dark", DARK],
] as const) {
  describe(`${name} theme`, () => {
    for (const surface of SURFACES) {
      for (const text of TEXT_TOKENS) {
        it(`${text} on ${surface} is at least 4.5:1`, () => {
          expect(contrast(token(theme, text), token(theme, surface))).toBeGreaterThanOrEqual(4.5);
        });
      }
      for (const mark of MARK_TOKENS) {
        it(`${mark} on ${surface} is at least 3:1`, () => {
          expect(contrast(token(theme, mark), token(theme, surface))).toBeGreaterThanOrEqual(3);
        });
      }
    }
    it("--on-accent on --accent is at least 4.5:1", () => {
      expect(contrast(token(theme, "--on-accent"), token(theme, "--accent"))).toBeGreaterThanOrEqual(4.5);
    });
  });
}

/*
 * Landing page (UI.md §16). Themed --lp-* pairs in both themes, and the theme-invariant --brand-*
 * pairs of the hero card (a light surface in both themes). [foreground, background, minimum].
 */
const LANDING_PAIRS: ReadonlyArray<readonly [string, string, number]> = [
  ...["--lp-bg", "--lp-panel", "--lp-panel-soft", "--lp-panel-paper", "--lp-card"].flatMap((surface) => [
    ["--lp-text", surface, 4.5] as const,
    ["--lp-muted", surface, 4.5] as const,
  ]),
  ["--lp-link", "--lp-bg", 4.5],
  ["--lp-on-button", "--lp-button", 4.5],
  ["--lp-on-ink", "--lp-ink", 4.5],
  ["--lp-on-ink-muted", "--lp-ink", 4.5],
  ["--lp-chip-text", "--lp-chip", 4.5],
  ["--lp-cat-chip-text", "--lp-cat-chip", 4.5],
  ...["--lp-relevant", "--lp-not-relevant", "--lp-save", "--lp-quiet"].flatMap((mark) => [
    [mark, "--lp-panel", 3] as const,
    [mark, "--lp-card", 3] as const,
  ]),
  ...["--lp-edge-cites", "--lp-edge-similar", "--lp-edge-topic", "--lp-edge-authored"].map(
    (edge) => [edge, "--lp-panel-paper", 3] as const,
  ),
  ["--brand-ink", "--brand-white", 4.5],
  ["--brand-ink", "--brand-tint", 4.5],
  ["--brand-muted", "--brand-white", 4.5],
  ["--brand-muted", "--brand-tint", 4.5],
  ["--brand-chip-ink", "--brand-accent-soft", 4.5],
  // Small white text on the cs.LG tile; the sticker's white text is large (≥ 19 px bold): 3:1.
  ["--brand-white", "--brand-accent-deep", 4.5],
  ["--brand-white", "--brand-accent", 3],
  // RELEVANT / SAVE stamps on the hero cards (large bold text).
  ["--brand-relevant", "--brand-white", 3],
  ["--brand-save", "--brand-white", 3],
];

for (const [name, theme] of [
  ["light", LIGHT],
  ["dark", DARK],
] as const) {
  describe(`landing tokens, ${name} theme`, () => {
    for (const [foreground, background, minimum] of LANDING_PAIRS) {
      it(`${foreground} on ${background} is at least ${minimum}:1`, () => {
        expect(contrast(token(theme, foreground), token(theme, background))).toBeGreaterThanOrEqual(minimum);
      });
    }
  });
}
