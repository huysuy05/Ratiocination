/** The landing's pill button: navy (inverted in dark), white caps text, a small blue dot, radius 4. */
import clsx from "clsx";
import type { ReactNode } from "react";
import { Link } from "react-router";

const pillClass =
  "group relative inline-flex h-11 items-center gap-2.5 rounded-[4px] bg-(--lp-button) px-5 text-[13px] font-bold tracking-[0.04em] text-(--lp-on-button) uppercase transition-[transform,box-shadow] duration-200 hover:-translate-y-px hover:shadow-[0_10px_24px_-12px_rgba(17,18,13,0.45)] active:translate-y-0";

function Dot() {
  return <span aria-hidden="true" className="size-[7px] shrink-0 rounded-full bg-current opacity-60 transition-transform duration-200 group-hover:scale-125" />;
}

/** In-page anchor (`href="#…"`) or app route (`to`). */
export function PillLink({ href, to, children, className }: { href?: string; to?: string; children: ReactNode; className?: string }) {
  if (to !== undefined) {
    return (
      <Link to={to} className={clsx(pillClass, className)}>
        <Dot />
        {children}
      </Link>
    );
  }
  return (
    <a href={href} className={clsx(pillClass, className)}>
      <Dot />
      {children}
    </a>
  );
}
