import clsx from "clsx";

export interface SpinnerProps {
  className?: string;
  /** Accessible name; omit for a decorative spinner (e.g. inside a busy button). */
  label?: string;
}

/** Small circular loading indicator (static ring under reduced motion). */
export function Spinner({ className, label }: SpinnerProps) {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      className={clsx("size-4 shrink-0 animate-spin", className)}
      role={label ? "img" : undefined}
      aria-label={label}
      aria-hidden={label ? undefined : true}
      focusable="false"
    >
      <circle cx="12" cy="12" r="9" stroke="currentColor" strokeOpacity="0.22" strokeWidth="2.5" />
      <path d="M21 12a9 9 0 0 0-9-9" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" />
    </svg>
  );
}
