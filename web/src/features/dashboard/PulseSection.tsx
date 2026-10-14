/** Pulse (UI.md §12.1): KPI cards with animated counters and change vs the previous period. */
import clsx from "clsx";
import { motion } from "motion/react";
import { Bookmark, Eye, Flame, Hand, Library, Percent, type LucideIcon } from "lucide-react";
import type { DashboardActivity } from "../../api/types";
import { AnimatedNumber } from "../../components/charts/AnimatedNumber";
import { Skeleton } from "../../components/ui/Skeleton";
import { staggerDelay, useMotionPreference } from "../../lib/motion";
import { DeltaChip } from "./DeltaChip";
import { countDelta, periodPhrase, rateDelta } from "./format";

interface Kpi {
  key: string;
  label: string;
  icon: LucideIcon;
  value: number;
  format?: (value: number) => string;
  delta?: { text: string; tone: "up" | "down" | "flat" | "none" };
  sub: string;
  color: string;
}

function KpiCard({ kpi, index }: { kpi: Kpi; index: number }) {
  const { reduced } = useMotionPreference();
  const Icon = kpi.icon;
  return (
    <motion.li
      initial={{ opacity: 0, y: 10 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.25, delay: staggerDelay(index, reduced) }}
      className="relative flex flex-col gap-2 overflow-hidden rounded-card border border-border bg-surface p-4 surface-sheen elevation-card"
    >
      <span
        aria-hidden="true"
        className="pointer-events-none absolute -top-10 -right-10 size-28 rounded-full blur-2xl"
        style={{ backgroundColor: `color-mix(in oklab, ${kpi.color} 16%, transparent)` }}
      />
      <div className="flex items-center gap-2">
        <span className="grid size-7 place-items-center rounded-control" style={{ backgroundColor: `color-mix(in oklab, ${kpi.color} 14%, transparent)`, color: kpi.color }}>
          <Icon aria-hidden="true" className="size-4" />
        </span>
        <h3 className="text-[12.5px] font-medium text-muted">{kpi.label}</h3>
      </div>
      <div className="flex items-end justify-between gap-2">
        <AnimatedNumber value={kpi.value} format={kpi.format} className="text-[30px] leading-none font-semibold tracking-[-0.02em] text-text" />
        {kpi.delta ? <DeltaChip text={kpi.delta.text} tone={kpi.delta.tone} label="vs the previous period" /> : null}
      </div>
      <p className="text-[12px] text-faint">{kpi.sub}</p>
    </motion.li>
  );
}

export function PulseSection({ activity, loading }: { activity: DashboardActivity | undefined; loading: boolean }) {
  if (loading || !activity) {
    return (
      <ul className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">
        {[0, 1, 2, 3, 4, 5].map((i) => (
          <li key={i}>
            <Skeleton className="h-[118px] rounded-card" />
          </li>
        ))}
      </ul>
    );
  }
  const { totals, previous, all_time: allTime } = activity;
  const span = periodPhrase(activity.period.days);
  const kpis: Kpi[] = [
    {
      key: "swiped",
      label: "Papers swiped",
      icon: Hand,
      value: totals.swiped,
      delta: countDelta(totals.swiped, previous.swiped),
      sub: `${span} · ${totals.opened} opened`,
      color: "var(--accent)",
    },
    {
      key: "rate",
      label: "Relevant rate",
      icon: Percent,
      value: (activity.positive_rate ?? 0) * 100,
      format: (value) => (activity.positive_rate === null ? "—" : `${Math.round(value)}%`),
      delta: rateDelta(activity.positive_rate, activity.positive_rate_before),
      sub: `${totals.relevant + totals.saved} of ${totals.swiped} kept`,
      color: "var(--relevant)",
    },
    {
      key: "saved",
      label: "Saved",
      icon: Bookmark,
      value: totals.saved,
      delta: countDelta(totals.saved, previous.saved),
      sub: "to read later",
      color: "var(--save)",
    },
    {
      key: "read",
      label: "Read",
      icon: Eye,
      value: totals.read,
      delta: countDelta(totals.read, previous.read),
      sub: `${allTime.read} read all time`,
      color: "var(--read)",
    },
    {
      key: "streak",
      label: "Streak",
      icon: Flame,
      value: activity.streak_days,
      format: (value) => `${Math.round(value)} day${Math.round(value) === 1 ? "" : "s"}`,
      sub: activity.streak_days > 0 ? "days in a row with a swipe" : "swipe today to start one",
      color: "var(--save)",
    },
    {
      key: "library",
      label: "Library",
      icon: Library,
      value: allTime.library,
      sub: `${allTime.swiped} swiped all time`,
      color: "var(--consolidating)",
    },
  ];
  return (
    <ul className={clsx("grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6")} aria-label="Key numbers">
      {kpis.map((kpi, index) => (
        <KpiCard key={kpi.key} kpi={kpi} index={index} />
      ))}
    </ul>
  );
}

