/**
 * Semantic colour tones shared by Badge, Chip and ProgressBar. A tone resolves to a theme token
 * (styles/tokens.css); components also accept a raw CSS colour (e.g. `STATE_META[state].color`).
 */
import type { CSSProperties } from "react";

export type Tone =
  | "neutral"
  | "accent"
  | "relevant"
  | "not-relevant"
  | "save"
  | "read"
  | "faint"
  | "disruptive"
  | "consolidating"
  | "authored";

export const TONE_COLOR: Readonly<Record<Tone, string>> = {
  neutral: "var(--text-muted)",
  accent: "var(--accent)",
  relevant: "var(--relevant)",
  "not-relevant": "var(--not-relevant)",
  save: "var(--save)",
  read: "var(--read)",
  faint: "var(--text-faint)",
  disruptive: "var(--disruptive)",
  consolidating: "var(--consolidating)",
  authored: "var(--edge-authored)",
};

/** Inline style object that may carry CSS custom properties (`--tone`, …). */
export type CSSVariables = CSSProperties & Record<`--${string}`, string | number | undefined>;

/** Resolve `color ?? tone` to a CSS colour value. */
export function toneColor(tone: Tone = "neutral", color?: string | null): string {
  return color ?? TONE_COLOR[tone];
}
