/**
 * Activity bar chart (UI.md §12.2): one bar per day or week; the full bar is everything swiped, the
 * highlighted part the positives. Bars grow from the baseline once; hovering or focusing the chart
 * shows a tooltip. A visually hidden table carries the same numbers for screen readers.
 */
import clsx from "clsx";
import { motion } from "motion/react";
import { useState } from "react";
import { useMotionPreference } from "../../lib/motion";

export interface BarDatum {
  key: string;
  label: string;
  total: number;
  highlight: number;
}

export interface ActivityBarsProps {
  data: readonly BarDatum[];
  label: string;
  totalLabel: string;
  highlightLabel: string;
  height?: number;
  className?: string;
}

const PLOT_TOP = 8;

export function ActivityBars({ data, label, totalLabel, highlightLabel, height = 180, className }: ActivityBarsProps) {
  const { reduced } = useMotionPreference();
  const [hover, setHover] = useState<number | null>(null);
  const width = 640;
  const max = Math.max(1, ...data.map((d) => d.total));
  const slot = width / Math.max(1, data.length);
  const barWidth = Math.max(2, Math.min(28, slot * 0.68));
  const plot = height - PLOT_TOP;
  const scale = (value: number) => (value / max) * plot;
  const active = hover !== null ? data[hover] : null;
  const ticks = [max, Math.round(max / 2)].filter((tick, index, all) => tick > 0 && all.indexOf(tick) === index);

  return (
    <figure className={clsx("relative flex flex-col gap-2", className)}>
      <div className="relative">
        <svg
          role="img"
          aria-label={label}
          viewBox={`0 0 ${width} ${height}`}
          preserveAspectRatio="none"
          className="h-[180px] w-full overflow-visible"
          onMouseLeave={() => setHover(null)}
        >
          {ticks.map((tick) => (
            <line key={tick} x1={0} x2={width} y1={height - scale(tick)} y2={height - scale(tick)} stroke="var(--border)" strokeDasharray="3 4" />
          ))}
          <line x1={0} x2={width} y1={height} y2={height} stroke="var(--border-strong)" />
          {data.map((datum, index) => {
            const x = index * slot + (slot - barWidth) / 2;
            const total = scale(datum.total);
            const highlight = scale(datum.highlight);
            const delay = reduced ? 0 : Math.min(index, 40) * 0.012;
            return (
              <g key={datum.key} onMouseEnter={() => setHover(index)}>
                <rect x={index * slot} y={0} width={slot} height={height} fill="transparent" />
                <motion.rect
                  x={x}
                  width={barWidth}
                  rx={Math.min(4, barWidth / 2)}
                  fill={hover === index ? "var(--surface-3)" : "color-mix(in oklab, var(--accent) 22%, var(--surface-2))"}
                  initial={reduced ? false : { height: 0, y: height }}
                  animate={{ height: total, y: height - total }}
                  transition={{ duration: 0.55, delay, ease: [0.22, 1, 0.36, 1] }}
                />
                <motion.rect
                  x={x}
                  width={barWidth}
                  rx={Math.min(4, barWidth / 2)}
                  fill="var(--accent)"
                  initial={reduced ? false : { height: 0, y: height }}
                  animate={{ height: highlight, y: height - highlight }}
                  transition={{ duration: 0.55, delay: delay + 0.08, ease: [0.22, 1, 0.36, 1] }}
                />
              </g>
            );
          })}
        </svg>
        {active && hover !== null ? (
          <div
            aria-hidden="true"
            className="pointer-events-none absolute -top-2 z-10 -translate-x-1/2 -translate-y-full rounded-control border border-border bg-surface px-2.5 py-1.5 text-[12px] whitespace-nowrap elevation-popover"
            style={{ left: `${((hover + 0.5) / data.length) * 100}%` }}
          >
            <p className="font-medium text-text">{active.label}</p>
            <p className="text-muted">
              {active.total} {totalLabel.toLowerCase()} · <span className="text-accent">{active.highlight} {highlightLabel.toLowerCase()}</span>
            </p>
          </div>
        ) : null}
        <div aria-hidden="true" className="pointer-events-none absolute inset-y-0 -left-1 flex -translate-x-full flex-col justify-between py-1 text-right text-[10.5px] text-faint tabular-nums">
          <span>{max}</span>
          <span>0</span>
        </div>
      </div>
      {data.length > 0 ? (
        <div aria-hidden="true" className="flex justify-between text-[11px] text-faint">
          <span>{data[0]!.label}</span>
          {data.length > 2 ? <span>{data[Math.floor(data.length / 2)]!.label}</span> : null}
          <span>{data[data.length - 1]!.label}</span>
        </div>
      ) : null}
      <table className="sr-only">
        <caption>{label}</caption>
        <thead>
          <tr>
            <th scope="col">Date</th>
            <th scope="col">{totalLabel}</th>
            <th scope="col">{highlightLabel}</th>
          </tr>
        </thead>
        <tbody>
          {data.map((datum) => (
            <tr key={datum.key}>
              <th scope="row">{datum.label}</th>
              <td>{datum.total}</td>
              <td>{datum.highlight}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </figure>
  );
}
