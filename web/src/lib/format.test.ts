import { describe, expect, it } from "vitest";
import {
  formatAuthors,
  formatBytes,
  formatDate,
  formatMonth,
  formatNumber,
  formatPercent,
  formatRelativeDate,
  formatScore,
  formatUsd,
  formatVersionLabel,
  parseDate,
  pluralize,
} from "./format";

// Local noon on 2026-10-12, so calendar math is independent of the machine's timezone.
const NOW = new Date(2026, 9, 12, 12, 0, 0);
const ago = (ms: number) => new Date(NOW.getTime() - ms).toISOString();
const MINUTE = 60_000;
const HOUR = 60 * MINUTE;

describe("formatRelativeDate", () => {
  it.each([
    ["2026-10-12", "today"],
    ["2026-10-11", "yesterday"],
    ["2026-10-09", "3 days ago"],
    ["2026-09-28", "2 weeks ago"],
    ["2026-07-01", "3 months ago"],
    ["2024-10-01", "2 years ago"],
    ["2026-10-14", "in 2 days"],
  ])("calendar date %s → %s", (value, expected) => {
    expect(formatRelativeDate(value, NOW)).toBe(expected);
  });

  it("formats datetimes with minutes and hours, then calendar days", () => {
    expect(formatRelativeDate(ago(10_000), NOW)).toBe("just now");
    expect(formatRelativeDate(ago(5 * MINUTE), NOW)).toBe("5 minutes ago");
    expect(formatRelativeDate(ago(3 * HOUR), NOW)).toBe("3 hours ago");
    expect(formatRelativeDate(ago(26 * HOUR), NOW)).toBe("yesterday");
  });

  it("returns an empty string for missing or invalid values", () => {
    expect(formatRelativeDate(null, NOW)).toBe("");
    expect(formatRelativeDate("not a date", NOW)).toBe("");
  });
});

describe("dates", () => {
  it("parses calendar dates without a timezone shift", () => {
    const date = parseDate("2026-10-01");
    expect(date?.getFullYear()).toBe(2026);
    expect(date?.getMonth()).toBe(9);
    expect(date?.getDate()).toBe(1);
  });

  it("formats dates and months", () => {
    expect(formatDate("2026-10-01")).toBe("Oct 1, 2026");
    expect(formatDate(undefined)).toBe("");
    expect(formatMonth("2026-10")).toBe("October 2026");
    expect(formatMonth("bad")).toBe("bad");
  });
});

describe("formatAuthors", () => {
  const authors = [
    { name: "Dana C", position: 2, openalex_id: null },
    { name: "Alex A", position: 0, openalex_id: null },
    { name: "Blake B", position: 1, openalex_id: null },
    { name: "Eli D", position: 3, openalex_id: null },
  ];

  it('shows the first three by position, then "et al."', () => {
    expect(formatAuthors(authors)).toBe("Alex A, Blake B, Dana C et al.");
  });

  it("lists everyone when there are at most three", () => {
    expect(formatAuthors(authors.slice(0, 3))).toBe("Alex A, Blake B, Dana C");
    expect(formatAuthors([{ name: "Solo Author", position: 0, openalex_id: null }])).toBe("Solo Author");
    expect(formatAuthors(["A", "B"])).toBe("A, B");
  });

  it("supports another limit and empty lists", () => {
    expect(formatAuthors(authors, 1)).toBe("Alex A et al.");
    expect(formatAuthors([])).toBe("");
    expect(formatAuthors(null)).toBe("");
  });
});

describe("numbers", () => {
  it("formats percentages from fractions", () => {
    expect(formatPercent(0.2)).toBe("20%");
    expect(formatPercent(0.125, 1)).toBe("12.5%");
    expect(formatPercent(null)).toBe("—");
  });

  it("formats counts, scores, money and sizes", () => {
    expect(formatNumber(12345)).toBe("12,345");
    expect(formatScore(0.41234)).toBe("0.41");
    expect(formatUsd(0.0034)).toBe("$0.0034");
    expect(formatUsd(1.5)).toBe("$1.50");
    expect(formatUsd(0)).toBe("$0.00");
    expect(formatBytes(512)).toBe("512 B");
    expect(formatBytes(1536)).toBe("1.5 KB");
    expect(formatBytes(50 * 1024 * 1024)).toBe("50 MB");
  });

  it("pluralizes and labels versions", () => {
    expect(pluralize(1, "paper")).toBe("1 paper");
    expect(pluralize(3, "paper")).toBe("3 papers");
    expect(formatVersionLabel("published")).toBe("Published");
    expect(formatVersionLabel("v3")).toBe("v3");
  });
});
