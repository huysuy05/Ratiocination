import clsx from "clsx";
import type { ReactNode } from "react";

export interface PageHeaderProps {
  title: ReactNode;
  /** Small label above the title (e.g. the project name). */
  eyebrow?: ReactNode;
  description?: ReactNode;
  /** Buttons on the right (wrap below on narrow screens). */
  actions?: ReactNode;
  className?: string;
}

/** Page title block: the page's single `<h1 id="page-title">`, a description and actions. */
export function PageHeader({ title, eyebrow, description, actions, className }: PageHeaderProps) {
  return (
    <header className={clsx("mb-6 flex flex-wrap items-end justify-between gap-x-6 gap-y-4 md:mb-8", className)}>
      <div className="flex min-w-0 flex-col gap-1.5">
        {eyebrow ? <p className="text-eyebrow">{eyebrow}</p> : null}
        <h1 id="page-title" className="font-serif text-[28px] leading-tight font-semibold tracking-[-0.015em] text-text md:text-[32px]">
          {title}
        </h1>
        {description ? <p className="max-w-2xl text-[14.5px] text-muted">{description}</p> : null}
      </div>
      {actions ? <div className="flex shrink-0 flex-wrap items-center gap-2">{actions}</div> : null}
    </header>
  );
}
