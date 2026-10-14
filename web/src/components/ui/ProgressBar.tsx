import clsx from "clsx";
import type { ComponentPropsWithRef } from "react";
import { formatPercent } from "../../lib/format";
import { toneColor, type CSSVariables, type Tone } from "./tones";

export type ProgressBarSize = "xs" | "sm" | "md";

const TRACK_SIZES: Readonly<Record<ProgressBarSize, string>> = {
  xs: "h-1",
  sm: "h-1.5",
  md: "h-2",
};

export interface ProgressBarProps extends Omit<ComponentPropsWithRef<"div">, "children"> {
  /** Fraction 0..1; null/undefined/NaN = indeterminate. */
  value: number | null | undefined;
  /** Accessible name, e.g. "Deck build progress". */
  label: string;
  tone?: Tone;
  /** Raw CSS colour (overrides `tone`). */
  color?: string;
  size?: ProgressBarSize;
  /** Show the percentage next to the bar. */
  showValue?: boolean;
  /** Overrides the spoken value (default "42%"). */
  valueText?: string;
  /** Marker position 0..1 (e.g. a budget's stop fraction). */
  marker?: number | null;
  /** Spoken description of the marker, e.g. "Stops at 80% of the daily budget". */
  markerLabel?: string;
}

function clampFraction(value: number): number {
  return Math.min(1, Math.max(0, value));
}

/** Determinate or indeterminate progress (role="progressbar"; UI.md §12). */
export function ProgressBar({
  value,
  label,
  tone = "accent",
  color,
  size = "sm",
  showValue = false,
  valueText,
  marker,
  markerLabel,
  className,
  style,
  ...rest
}: ProgressBarProps) {
  const determinate = typeof value === "number" && Number.isFinite(value);
  const fraction = determinate ? clampFraction(value) : 0;
  const percent = Math.round(fraction * 100);
  const vars: CSSVariables = { ...style, "--tone": toneColor(tone, color) };
  const markerFraction = typeof marker === "number" && Number.isFinite(marker) ? clampFraction(marker) : null;

  return (
    <div {...rest} style={vars} className={clsx("flex w-full items-center gap-3", className)}>
      <div
        role="progressbar"
        aria-label={label}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={determinate ? percent : undefined}
        aria-valuetext={valueText ?? (determinate ? formatPercent(fraction) : undefined)}
        data-state={determinate ? "determinate" : "indeterminate"}
        className={clsx("relative min-w-0 flex-1 overflow-hidden rounded-pill bg-surface-3", TRACK_SIZES[size])}
      >
        {determinate ? (
          <div
            className="absolute inset-y-0 left-0 rounded-pill bg-(--tone) shadow-[0_0_12px_-1px_color-mix(in_oklab,var(--tone)_60%,transparent)] transition-[width] duration-500 ease-smooth"
            style={{ width: `${fraction * 100}%` }}
          />
        ) : (
          <div
            className={clsx(
              "absolute inset-y-0 left-0 w-2/5 rounded-pill bg-[linear-gradient(90deg,transparent,var(--tone),transparent)] animate-indeterminate",
              "reduced-motion:w-full reduced-motion:bg-[repeating-linear-gradient(135deg,color-mix(in_oklab,var(--tone)_45%,transparent)_0_6px,transparent_6px_12px)]",
            )}
          />
        )}
        {markerFraction !== null ? (
          <span
            aria-hidden="true"
            className="absolute inset-y-0 w-0.5 -translate-x-1/2 bg-text/70"
            style={{ left: `${markerFraction * 100}%` }}
          />
        ) : null}
      </div>
      {markerLabel ? <span className="sr-only">{markerLabel}</span> : null}
      {showValue && determinate ? (
        <span aria-hidden="true" className="shrink-0 text-xs font-medium text-muted tabular-nums">
          {percent}%
        </span>
      ) : null}
    </div>
  );
}
