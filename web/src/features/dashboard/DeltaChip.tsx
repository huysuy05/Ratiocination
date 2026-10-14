import clsx from "clsx";
import { ArrowDownRight, ArrowUpRight, Minus } from "lucide-react";
import type { DeltaTone } from "./format";

export function DeltaChip({ text, tone, label }: { text: string; tone: DeltaTone; label: string }) {
  const Icon = tone === "up" ? ArrowUpRight : tone === "down" ? ArrowDownRight : Minus;
  return (
    <span
      title={label}
      className={clsx(
        "inline-flex h-6 items-center gap-0.5 rounded-pill px-2 text-[11.5px] font-semibold tabular-nums",
        tone === "up" && "bg-relevant/12 text-relevant",
        tone === "down" && "bg-not-relevant/12 text-not-relevant",
        (tone === "flat" || tone === "none") && "bg-surface-3 text-muted",
      )}
    >
      {tone === "none" ? null : <Icon aria-hidden="true" className="size-3" />}
      {text}
      <span className="sr-only"> {label}</span>
    </span>
  );
}
