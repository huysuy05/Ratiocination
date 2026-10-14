/**
 * A small line chart (12-week trends). Null values break the line. The line traces in once; an
 * optional soft area sits under it. `role="img"` with a summary label.
 */
import { motion } from "motion/react";
import { useId } from "react";
import { useMotionPreference } from "../../lib/motion";

export interface SparklineProps {
  values: readonly (number | null)[];
  label: string;
  width?: number;
  height?: number;
  color?: string;
  /** Fixed y range (default: 0..max). */
  min?: number;
  max?: number;
  area?: boolean;
  className?: string;
}

export function sparkPoints(values: readonly (number | null)[], width: number, height: number, min: number, max: number) {
  const span = max - min || 1;
  const step = values.length > 1 ? width / (values.length - 1) : 0;
  return values.map((value, index) =>
    value === null ? null : { x: index * step, y: height - 2 - ((value - min) / span) * (height - 4) },
  );
}

/** SVG path data; a null starts a new segment. */
export function sparkPath(points: readonly ({ x: number; y: number } | null)[]): string {
  let d = "";
  let drawing = false;
  for (const point of points) {
    if (!point) {
      drawing = false;
      continue;
    }
    d += `${drawing ? "L" : "M"}${point.x.toFixed(1)} ${point.y.toFixed(1)} `;
    drawing = true;
  }
  return d.trim();
}

export function Sparkline({ values, label, width = 120, height = 32, color = "var(--accent)", min, max, area = true, className }: SparklineProps) {
  const { reduced } = useMotionPreference();
  const gradient = `spark-${useId().replace(/:/g, "")}`;
  const numbers = values.filter((v): v is number => v !== null);
  const lo = min ?? 0;
  const hi = max ?? Math.max(lo + 1e-9, ...numbers);
  const points = sparkPoints(values, width, height, lo, hi);
  const line = sparkPath(points);
  const solid = points.filter((p): p is { x: number; y: number } => p !== null);
  const areaPath =
    area && solid.length > 1 && values.every((v) => v !== null)
      ? `${line} L${solid[solid.length - 1]!.x.toFixed(1)} ${height} L${solid[0]!.x.toFixed(1)} ${height} Z`
      : null;
  const last = solid[solid.length - 1];
  return (
    <svg role="img" aria-label={label} viewBox={`0 0 ${width} ${height}`} width={width} height={height} className={className} preserveAspectRatio="none">
      <defs>
        <linearGradient id={gradient} x1="0" x2="0" y1="0" y2="1">
          <stop offset="0%" stopColor={color} stopOpacity="0.28" />
          <stop offset="100%" stopColor={color} stopOpacity="0" />
        </linearGradient>
      </defs>
      {areaPath ? <path d={areaPath} fill={`url(#${gradient})`} /> : null}
      {line ? (
        <motion.path
          d={line}
          fill="none"
          stroke={color}
          strokeWidth={1.75}
          strokeLinecap="round"
          strokeLinejoin="round"
          initial={reduced ? false : { pathLength: 0 }}
          animate={{ pathLength: 1 }}
          transition={{ duration: 0.9, ease: "easeOut" }}
        />
      ) : null}
      {last ? <circle cx={last.x} cy={last.y} r={2.5} fill={color} /> : null}
    </svg>
  );
}
