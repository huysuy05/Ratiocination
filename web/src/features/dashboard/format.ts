/** Dashboard formatting helpers (pure). */
import type { DashboardDays, Period } from "../../api/types";

export const DAY_OPTIONS: readonly DashboardDays[] = [7, 30, 90, 365];

export function parseDays(raw: string | null): DashboardDays {
  const value = Number(raw);
  return (DAY_OPTIONS as readonly number[]).includes(value) ? (value as DashboardDays) : 30;
}

/** "this week", "in the last 30 days", "in the last 90 days", "this year". */
export function periodPhrase(days: number): string {
  return days === 7 ? "this week" : days === 365 ? "this year" : `in the last ${days} days`;
}

/** "Oct 3" for a calendar date (no time-zone shift). */
export function shortDate(iso: string): string {
  const [y, m, d] = iso.split("-").map(Number);
  if (!y || !m || !d) return iso;
  return new Intl.DateTimeFormat("en", { month: "short", day: "numeric" }).format(new Date(y, m - 1, d));
}

export function bucketLabel(iso: string, period: Pick<Period, "bucket">): string {
  return period.bucket === "week" ? `Week of ${shortDate(iso)}` : shortDate(iso);
}

export type DeltaTone = "up" | "down" | "flat" | "none";

/** Change of a count vs the previous period: "+40%", "−25%", "new" or "—". */
export function countDelta(now: number, before: number): { text: string; tone: DeltaTone } {
  if (before === 0) return now > 0 ? { text: "new", tone: "up" } : { text: "—", tone: "none" };
  const change = (now - before) / before;
  if (Math.abs(change) < 0.005) return { text: "±0%", tone: "flat" };
  const pct = Math.round(Math.abs(change) * 100);
  return { text: `${change > 0 ? "+" : "−"}${pct}%`, tone: change > 0 ? "up" : "down" };
}

/** Change of a rate in percentage points: "+12 pts", or "—" when either side is unknown. */
export function rateDelta(now: number | null, before: number | null): { text: string; tone: DeltaTone } {
  if (now === null || before === null) return { text: "—", tone: "none" };
  const points = Math.round((now - before) * 100);
  if (points === 0) return { text: "±0 pts", tone: "flat" };
  return { text: `${points > 0 ? "+" : "−"}${Math.abs(points)} pts`, tone: points > 0 ? "up" : "down" };
}

export function percent(value: number | null | undefined, digits = 0): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  return `${(value * 100).toFixed(digits)}%`;
}
