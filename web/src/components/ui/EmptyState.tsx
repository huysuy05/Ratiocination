import clsx from "clsx";
import type { ComponentPropsWithRef, ReactNode } from "react";

export type EmptyStateSize = "sm" | "md" | "lg";

export interface EmptyStateProps extends Omit<ComponentPropsWithRef<"div">, "title"> {
  title: ReactNode;
  description?: ReactNode;
  /** Icon in the centre of the scope illustration (lucide icon element). */
  icon?: ReactNode;
  /** Buttons/links. */
  actions?: ReactNode;
  size?: EmptyStateSize;
  /** Heading level of the title (default 2). */
  headingLevel?: 2 | 3;
}

const SCOPES: Readonly<Record<EmptyStateSize, string>> = {
  sm: "size-20",
  md: "size-28",
  lg: "size-36",
};

/** Empty/zero state with a static radar illustration (UI.md §2, §9 "Create your first project"). */
export function EmptyState({
  title,
  description,
  icon,
  actions,
  size = "md",
  headingLevel = 2,
  className,
  ...rest
}: EmptyStateProps) {
  const Heading = headingLevel === 2 ? "h2" : "h3";
  return (
    <div {...rest} className={clsx("flex flex-col items-center px-4 py-10 text-center", className)}>
      <div aria-hidden="true" className={clsx("relative grid place-items-center", SCOPES[size])}>
        <span className="absolute inset-0 rounded-full border border-accent/15 bg-[radial-gradient(closest-side,color-mix(in_oklab,var(--accent)_10%,transparent),transparent)]" />
        <span className="absolute inset-[18%] rounded-full border border-accent/20" />
        <span className="absolute inset-[36%] rounded-full border border-accent/25" />
        <span className="absolute inset-0 rounded-full bg-[conic-gradient(from_0deg,transparent_0deg,transparent_290deg,color-mix(in_oklab,var(--accent)_24%,transparent)_359deg,transparent_360deg)] rotate-[50deg]" />
        <span className="absolute top-[22%] right-[24%] size-1.5 rounded-full bg-accent shadow-[0_0_10px_var(--accent)]" />
        {icon ? (
          <span className="relative grid size-11 place-items-center rounded-[14px] border border-border-strong bg-surface text-accent elevation-card [&_svg]:size-5">
            {icon}
          </span>
        ) : (
          <span className="relative size-2 rounded-full bg-accent shadow-[0_0_12px_var(--accent)]" />
        )}
      </div>
      <Heading className={clsx("mt-6 font-semibold tracking-[-0.015em] text-text", size === "sm" ? "text-base" : "text-lg")}>{title}</Heading>
      {description ? <div className="mt-2 max-w-md text-sm text-muted">{description}</div> : null}
      {actions ? <div className="mt-6 flex flex-wrap items-center justify-center gap-2">{actions}</div> : null}
    </div>
  );
}
