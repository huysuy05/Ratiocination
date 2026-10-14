/**
 * A horizontal stacked bar of non-negative parts (score breakdowns, splits). Segments grow from the
 * left once (Motion), each with a tooltip-free text alternative in `aria-label`; zero parts are
 * omitted. `total` sets the bar's full width (default: the sum of the parts).
 */
import clsx from "clsx";
import { motion } from "motion/react";
import { useMotionPreference } from "../../lib/motion";

export interface StackedPart {
  key: string;
  label: string;
  value: number;
  color: string;
  /** Diagonal hatching (e.g. "not available"). */
  hatched?: boolean;
}

export interface StackedBarProps {
  parts: readonly StackedPart[];
  total?: number;
  label: string;
  height?: number;
  className?: string;
}

export function StackedBar({ parts, total, label, height = 12, className }: StackedBarProps) {
  const { reduced } = useMotionPreference();
  const sum = total ?? parts.reduce((acc, part) => acc + Math.max(0, part.value), 0);
  const visible = parts.filter((part) => part.value > 0);
  return (
    <div
      role="img"
      aria-label={`${label}: ${visible.map((part) => `${part.label} ${Math.round((part.value / (sum || 1)) * 100)}%`).join(", ") || "nothing"}`}
      className={clsx("flex w-full overflow-hidden rounded-pill bg-surface-3", className)}
      style={{ height }}
    >
      {visible.map((part, index) => (
        <motion.span
          key={part.key}
          initial={reduced ? false : { width: 0 }}
          animate={{ width: `${(part.value / (sum || 1)) * 100}%` }}
          transition={{ duration: 0.6, delay: reduced ? 0 : index * 0.06, ease: [0.22, 1, 0.36, 1] }}
          className={clsx("h-full shrink-0", index > 0 && "border-l-2 border-surface")}
          style={{
            backgroundColor: part.color,
            backgroundImage: part.hatched
              ? "repeating-linear-gradient(135deg, transparent 0 4px, color-mix(in oklab, var(--surface) 55%, transparent) 4px 8px)"
              : undefined,
          }}
        />
      ))}
    </div>
  );
}

/** Legend for a StackedBar: colour dot, label and a formatted value per part. */
export function StackedLegend({ parts, format }: { parts: readonly StackedPart[]; format: (value: number) => string }) {
  return (
    <ul className="flex flex-wrap gap-x-4 gap-y-1.5 text-[12.5px]">
      {parts.map((part) => (
        <li key={part.key} className="flex items-center gap-1.5">
          <span aria-hidden="true" className="size-2.5 rounded-full" style={{ backgroundColor: part.color }} />
          <span className="text-muted">{part.label}</span>
          <span className="font-medium text-text tabular-nums">{format(part.value)}</span>
        </li>
      ))}
    </ul>
  );
}
