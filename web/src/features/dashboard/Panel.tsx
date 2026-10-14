import clsx from "clsx";
import type { ReactNode } from "react";

/** A dashboard section card: icon + title + optional subtitle and actions. */
export function Panel({
  title,
  subtitle,
  icon,
  actions,
  children,
  className,
}: {
  title: string;
  subtitle?: ReactNode;
  icon?: ReactNode;
  actions?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <section className={clsx("flex flex-col gap-5 rounded-card border border-border bg-surface p-5 surface-sheen elevation-card md:p-6", className)}>
      <header className="flex flex-wrap items-start justify-between gap-3">
        <div className="flex min-w-0 items-start gap-3">
          {icon ? <span className="mt-0.5 grid size-8 shrink-0 place-items-center rounded-control bg-accent/12 text-accent [&_svg]:size-4">{icon}</span> : null}
          <div className="min-w-0">
            <h2 className="text-[17px] font-semibold tracking-[-0.01em] text-text">{title}</h2>
            {subtitle ? <p className="mt-0.5 text-[13px] text-muted">{subtitle}</p> : null}
          </div>
        </div>
        {actions ? <div className="flex shrink-0 items-center gap-2">{actions}</div> : null}
      </header>
      {children}
    </section>
  );
}
