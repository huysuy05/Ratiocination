import type { ReactNode } from "react";

export function SettingsCard({ title, description, children }: { title: string; description?: ReactNode; children: ReactNode }) {
  return (
    <section className="flex flex-col gap-4 rounded-card border border-border bg-surface p-5 surface-sheen elevation-card md:p-6">
      <header className="flex flex-col gap-1">
        <h2 className="text-[16px] font-semibold text-text">{title}</h2>
        {description ? <p className="text-[13px] text-muted">{description}</p> : null}
      </header>
      {children}
    </section>
  );
}
