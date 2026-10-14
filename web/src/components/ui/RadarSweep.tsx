import clsx from "clsx";
import { useReducedMotionConfig } from "motion/react";
import type { ComponentPropsWithRef, ReactNode } from "react";
import { useMotionPreference } from "../../lib/motion";
import { ProgressBar } from "./ProgressBar";
import type { CSSVariables } from "./tones";

export type RadarSweepSize = "sm" | "md" | "lg";

const SCOPE_SIZES: Readonly<Record<RadarSweepSize, string>> = {
  sm: "size-24",
  md: "size-40",
  lg: "size-56",
};

/** Decorative blips: angle (deg, clockwise from north) and radius (fraction of the scope radius). */
const BLIPS: readonly { angle: number; radius: number }[] = [
  { angle: 38, radius: 0.62 },
  { angle: 112, radius: 0.36 },
  { angle: 205, radius: 0.74 },
  { angle: 296, radius: 0.5 },
  { angle: 330, radius: 0.82 },
];

const SWEEP_SECONDS = 2.4;

function blipPosition(angle: number, radius: number): { left: string; top: string } {
  const radians = (angle * Math.PI) / 180;
  return {
    left: `${50 + Math.sin(radians) * radius * 50}%`,
    top: `${50 - Math.cos(radians) * radius * 50}%`,
  };
}

/** True when animations should be reduced: the app preference (`rr.motion`) or Motion's config. */
export function useReducedMotionPreference(): boolean {
  const { reduced } = useMotionPreference();
  const configReduced = useReducedMotionConfig();
  return reduced || configReduced === true;
}

export interface RadarScopeProps {
  size?: RadarSweepSize;
  /** Rotating sweep (2.4 s per turn). False renders a static scope. */
  animated?: boolean;
  className?: string;
}

/** The radar scope graphic (decorative). `RadarSweep` adds the message and progress. */
export function RadarScope({ size = "md", animated = true, className }: RadarScopeProps) {
  const reduced = useReducedMotionPreference();
  const spinning = animated && !reduced;
  return (
    <div
      aria-hidden="true"
      data-animated={spinning ? "true" : "false"}
      className={clsx(
        "relative shrink-0 overflow-hidden rounded-full border border-accent/25",
        "bg-[radial-gradient(closest-side,color-mix(in_oklab,var(--accent)_12%,transparent),transparent_85%)]",
        "shadow-[0_0_0_6px_color-mix(in_oklab,var(--accent)_5%,transparent),0_24px_60px_-24px_var(--accent-glow)]",
        SCOPE_SIZES[size],
        className,
      )}
    >
      {/* range rings + crosshair */}
      <span className="absolute inset-[17%] rounded-full border border-accent/15" />
      <span className="absolute inset-[34%] rounded-full border border-accent/20" />
      <span className="absolute inset-y-0 left-1/2 w-px -translate-x-1/2 bg-accent/10" />
      <span className="absolute inset-x-0 top-1/2 h-px -translate-y-1/2 bg-accent/10" />
      {/* sweep */}
      <span
        data-sweep=""
        className={clsx(
          "absolute inset-0 rounded-full",
          "bg-[conic-gradient(from_0deg,transparent_0deg,transparent_250deg,color-mix(in_oklab,var(--accent)_10%,transparent)_300deg,color-mix(in_oklab,var(--accent)_42%,transparent)_359deg,transparent_360deg)]",
          spinning ? "animate-sweep" : "rotate-[60deg]",
        )}
      >
        <span className="absolute top-0 left-1/2 h-1/2 w-[1.5px] -translate-x-1/2 bg-[linear-gradient(to_top,var(--accent),color-mix(in_oklab,var(--accent)_30%,transparent))] shadow-[0_0_10px_var(--accent)]" />
      </span>
      {/* blips light up as the sweep passes */}
      {BLIPS.map((blip) => {
        const position = blipPosition(blip.angle, blip.radius);
        return (
          <span
            key={blip.angle}
            className={clsx(
              "absolute size-1.5 -translate-x-1/2 -translate-y-1/2 rounded-full bg-accent shadow-[0_0_8px_var(--accent)]",
              spinning ? "opacity-20 animate-[rr-blip_var(--motion-sweep)_linear_infinite]" : "opacity-60",
            )}
            style={{
              ...position,
              animationDelay: spinning ? `${((blip.angle / 360) * SWEEP_SECONDS).toFixed(3)}s` : undefined,
            }}
          />
        );
      })}
      <span className="absolute top-1/2 left-1/2 size-2 -translate-x-1/2 -translate-y-1/2 rounded-full bg-accent shadow-[0_0_12px_var(--accent)]" />
    </div>
  );
}

export interface RadarSweepProps extends Omit<ComponentPropsWithRef<"div">, "title" | "children"> {
  /** Live job message (`job.message`), announced politely. */
  message?: ReactNode;
  /** Job progress 0..1; null/undefined = indeterminate. */
  progress?: number | null;
  /** Bold line above the message, e.g. "Building your deck". */
  title?: ReactNode;
  /** Accessible name of the progress bar. */
  label?: string;
  size?: RadarSweepSize;
  /** Show the progress bar (default true). */
  showProgress?: boolean;
  /** Extra content under the progress (e.g. a Cancel button). */
  children?: ReactNode;
}

/**
 * Job-in-progress state (UI.md §3, §6): a rotating radar sweep (2.4 s per turn — the only infinite
 * animation, use it only while a job runs) with the job message and a progress bar. Under reduced
 * motion it renders a static scope with the same message and progress.
 */
export function RadarSweep({
  message,
  progress,
  title,
  label = "Progress",
  size = "md",
  showProgress = true,
  className,
  style,
  children,
  ...rest
}: RadarSweepProps) {
  const reduced = useReducedMotionPreference();
  const vars: CSSVariables = { ...style };
  return (
    <div
      {...rest}
      style={vars}
      data-reduced={reduced ? "true" : "false"}
      className={clsx("flex flex-col items-center gap-5 text-center", className)}
    >
      <RadarScope size={size} animated={!reduced} />
      <div role="status" aria-live="polite" className="flex max-w-sm flex-col items-center gap-1">
        {title ? <p className="text-[15px] font-semibold tracking-[-0.01em] text-text">{title}</p> : null}
        <p className="text-sm text-muted">{message ?? "Working…"}</p>
      </div>
      {showProgress ? <ProgressBar value={progress} label={label} showValue className="w-full max-w-xs" /> : null}
      {children}
    </div>
  );
}

export interface RadarBackdropProps {
  className?: string;
  /** Distance between rings in px (default 72). */
  ringGap?: number;
}

/** Faint concentric rings + glow behind content (UI.md §2 "behind the deck"); CSS only, decorative. */
export function RadarBackdrop({ className, ringGap = 72 }: RadarBackdropProps) {
  const vars: CSSVariables = { "--ring-gap": `${ringGap}px` };
  return <div aria-hidden="true" style={vars} className={clsx("pointer-events-none absolute inset-0 radar-rings", className)} />;
}
