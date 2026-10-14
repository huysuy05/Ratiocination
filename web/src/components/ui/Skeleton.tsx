import clsx from "clsx";
import type { ComponentPropsWithRef, ReactNode } from "react";

export interface SkeletonProps extends ComponentPropsWithRef<"div"> {
  shape?: "rect" | "text" | "circle";
}

/**
 * Loading placeholder with a soft shimmer (static under reduced motion). Decorative: wrap loading
 * areas in `LoadingRegion` (or set aria-busy) so assistive tech hears "Loading".
 */
export function Skeleton({ shape = "rect", className, ...rest }: SkeletonProps) {
  return (
    <div
      aria-hidden="true"
      data-skeleton=""
      {...rest}
      className={clsx(
        "block bg-surface-2 bg-[linear-gradient(90deg,transparent_20%,color-mix(in_oklab,var(--surface-3)_90%,transparent)_50%,transparent_80%)]",
        "animate-shimmer bg-[length:220%_100%] bg-no-repeat",
        shape === "circle" ? "rounded-full" : shape === "text" ? "h-3.5 rounded-md" : "rounded-control",
        className,
      )}
    />
  );
}

export interface SkeletonTextProps {
  lines?: number;
  className?: string;
}

/** A paragraph of skeleton lines; the last one is shorter. */
export function SkeletonText({ lines = 3, className }: SkeletonTextProps) {
  const count = Math.max(1, Math.floor(lines));
  return (
    <div aria-hidden="true" className={clsx("flex flex-col gap-2.5", className)}>
      {Array.from({ length: count }, (_, index) => (
        <Skeleton key={index} shape="text" className={index === count - 1 && count > 1 ? "w-3/5" : "w-full"} />
      ))}
    </div>
  );
}

export interface LoadingRegionProps extends ComponentPropsWithRef<"div"> {
  /** Announced to screen readers, e.g. "Loading library". */
  label?: string;
  children?: ReactNode;
}

/** `role="status"` + `aria-busy` wrapper around skeletons. */
export function LoadingRegion({ label = "Loading", className, children, ...rest }: LoadingRegionProps) {
  return (
    <div {...rest} role="status" aria-live="polite" aria-busy="true" className={className}>
      <span className="sr-only">{label}</span>
      {children}
    </div>
  );
}
