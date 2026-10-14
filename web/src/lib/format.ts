/**
 * Display formatting (FROZEN). English locale throughout; dates given as "YYYY-MM-DD" are calendar
 * dates (no timezone shift), ISO datetimes are instants shown in local time.
 */
import type { Author } from "../api/types";

const LOCALE = "en";
const DATE_ONLY = /^(\d{4})-(\d{2})-(\d{2})$/;

/** True for a bare calendar date "YYYY-MM-DD". */
export function isDateOnly(value: string): boolean {
  return DATE_ONLY.test(value);
}

/** Parse an ISODate (as a local calendar date) or ISODateTime; null when missing or invalid. */
export function parseDate(value: string | Date | null | undefined): Date | null {
  if (value === null || value === undefined || value === "") return null;
  if (value instanceof Date) return Number.isNaN(value.getTime()) ? null : value;
  const match = DATE_ONLY.exec(value);
  if (match) {
    const [, y, m, d] = match;
    const date = new Date(Number(y), Number(m) - 1, Number(d));
    return Number.isNaN(date.getTime()) ? null : date;
  }
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? null : date;
}

function startOfDay(date: Date): number {
  return new Date(date.getFullYear(), date.getMonth(), date.getDate()).getTime();
}

/** Whole calendar days from `now` to `date` (negative = past). DST-safe. */
export function calendarDaysBetween(date: Date, now: Date): number {
  return Math.round((startOfDay(date) - startOfDay(now)) / 86_400_000);
}

function relativeDays(days: number, rtf: Intl.RelativeTimeFormat): string {
  const abs = Math.abs(days);
  if (abs < 7) return rtf.format(days, "day");
  if (abs < 30) return rtf.format(Math.round(days / 7), "week");
  if (abs < 365) return rtf.format(Math.round(days / 30), "month");
  return rtf.format(Math.round(days / 365), "year");
}

/**
 * Relative time: "just now", "5 minutes ago", "3 hours ago", "today", "yesterday", "3 days ago",
 * "2 weeks ago", "4 months ago", "in 2 days". Calendar dates never show hours. "" when invalid.
 */
export function formatRelativeDate(value: string | Date | null | undefined, now: Date = new Date()): string {
  const date = parseDate(value);
  if (!date) return "";
  const rtf = new Intl.RelativeTimeFormat(LOCALE, { numeric: "auto" });
  if (typeof value === "string" && isDateOnly(value)) return relativeDays(calendarDaysBetween(date, now), rtf);
  const seconds = Math.round((date.getTime() - now.getTime()) / 1000);
  const abs = Math.abs(seconds);
  if (abs < 45) return "just now";
  if (abs < 45 * 60) return rtf.format(Math.round(seconds / 60), "minute");
  if (abs < 22 * 3600) return rtf.format(Math.round(seconds / 3600), "hour");
  return relativeDays(calendarDaysBetween(date, now), rtf);
}

/** "Oct 12, 2026" ("" when invalid). */
export function formatDate(value: string | Date | null | undefined): string {
  const date = parseDate(value);
  if (!date) return "";
  return new Intl.DateTimeFormat(LOCALE, { year: "numeric", month: "short", day: "numeric" }).format(date);
}

/** "Oct 12, 2026, 4:05 PM" ("" when invalid). Calendar dates render without a time. */
export function formatDateTime(value: string | Date | null | undefined): string {
  const date = parseDate(value);
  if (!date) return "";
  if (typeof value === "string" && isDateOnly(value)) return formatDate(date);
  return new Intl.DateTimeFormat(LOCALE, { dateStyle: "medium", timeStyle: "short" }).format(date);
}

/** "2026-10" → "October 2026" (returns the input when it is not YYYY-MM). */
export function formatMonth(month: string): string {
  const match = /^(\d{4})-(\d{2})$/.exec(month);
  if (!match) return month;
  const date = new Date(Number(match[1]), Number(match[2]) - 1, 1);
  return new Intl.DateTimeFormat(LOCALE, { year: "numeric", month: "long" }).format(date);
}

/**
 * Author list for bylines: the first `max` authors (by position) joined with ", ", then " et al."
 * when there are more. "" for no authors.
 */
export function formatAuthors(authors: readonly (Author | string)[] | null | undefined, max = 3): string {
  if (!authors || authors.length === 0) return "";
  const names = [...authors]
    .map((author, index) =>
      typeof author === "string" ? { name: author, position: index } : { name: author.name, position: author.position },
    )
    .sort((a, b) => a.position - b.position)
    .map((author) => author.name.trim())
    .filter(Boolean);
  if (names.length <= max) return names.join(", ");
  return `${names.slice(0, Math.max(1, max)).join(", ")} et al.`;
}

/** 0.2 → "20%"; `digits` decimals at most. "—" for null/NaN. */
export function formatPercent(fraction: number | null | undefined, digits = 0): string {
  if (fraction === null || fraction === undefined || Number.isNaN(fraction)) return "—";
  return new Intl.NumberFormat(LOCALE, { style: "percent", maximumFractionDigits: digits }).format(fraction);
}

/** Thousands separators: 12345 → "12,345". "—" for null/NaN. */
export function formatNumber(value: number | null | undefined, digits = 0): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  return new Intl.NumberFormat(LOCALE, { maximumFractionDigits: digits }).format(value);
}

/** Model scores and weights: fixed decimals (default 2). "—" for null/NaN. */
export function formatScore(value: number | null | undefined, digits = 2): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  return value.toFixed(digits);
}

/** US dollars; amounts under one cent keep 4 decimals ("$0.0034"). "—" for null/NaN. */
export function formatUsd(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  const small = value !== 0 && Math.abs(value) < 0.01;
  return new Intl.NumberFormat(LOCALE, {
    style: "currency",
    currency: "USD",
    minimumFractionDigits: small ? 4 : 2,
    maximumFractionDigits: small ? 4 : 2,
  }).format(value);
}

/** 1536 → "1.5 KB". */
export function formatBytes(bytes: number | null | undefined): string {
  if (bytes === null || bytes === undefined || Number.isNaN(bytes)) return "—";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let value = Math.max(0, bytes);
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024;
    unit += 1;
  }
  const digits = unit === 0 || value >= 10 ? 0 : 1;
  return `${value.toFixed(digits)} ${units[unit]}`;
}

/** "1 paper" / "3 papers" (custom plural optional). */
export function pluralize(count: number, singular: string, plural = `${singular}s`): string {
  return `${formatNumber(count)} ${count === 1 ? singular : plural}`;
}

/** API.md: display label = version_label === "published" ? "Published" : version_label. */
export function formatVersionLabel(label: string): string {
  return label === "published" ? "Published" : label;
}
