import type { ReactNode } from "react";

export function AuthHeading({ title, subtitle }: { title: string; subtitle?: ReactNode }) {
  return (
    <header className="mb-6 flex flex-col gap-1.5 text-center">
      <h1 className="font-serif text-[26px] leading-tight font-semibold tracking-[-0.015em] text-text">{title}</h1>
      {subtitle ? <p className="text-[14px] text-muted">{subtitle}</p> : null}
    </header>
  );
}

export function FormAlert({ children, tone = "error" }: { children: ReactNode; tone?: "error" | "info" | "success" }) {
  const styles =
    tone === "error"
      ? "border-not-relevant/30 bg-not-relevant/10 text-not-relevant"
      : tone === "success"
        ? "border-relevant/30 bg-relevant/10 text-relevant"
        : "border-border-strong bg-surface-2 text-muted";
  return (
    <div role={tone === "error" ? "alert" : "status"} className={`mb-4 rounded-control border px-3.5 py-2.5 text-[13.5px] ${styles}`}>
      {children}
    </div>
  );
}

export function OrDivider() {
  return (
    <div className="my-5 flex items-center gap-3 text-[12px] font-medium tracking-wide text-faint uppercase" aria-hidden="true">
      <span className="h-px flex-1 bg-border" />
      or
      <span className="h-px flex-1 bg-border" />
    </div>
  );
}
