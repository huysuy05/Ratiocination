import clsx from "clsx";
import { ChevronRight, ShieldCheck } from "lucide-react";
import { Link } from "react-router";
import { useMe } from "../../api/hooks";
import { Tooltip } from "../../components/ui/Tooltip";
import { paths } from "../paths";

/** Badge text: a single-user install runs locally; an accounts server keeps documents private. */
function usePrivacyCopy(): { label: string; hint: string } {
  const { data } = useMe();
  return data?.mode === "accounts"
    ? { label: "Private", hint: "Your documents stay on this server" }
    : { label: "Local-only", hint: "Your documents stay on this machine" };
}

/** Privacy badge linking to Settings (UI.md §4). `compact` = shield icon only. */
export function PrivacyBadge({ compact = false, className }: { compact?: boolean; className?: string }) {
  const { label, hint } = usePrivacyCopy();
  const ariaLabel = `Privacy: ${label}. ${hint}. Open settings`;

  if (compact) {
    return (
      <Tooltip content={`${label} · ${hint}`} side="right">
        <Link
          to={paths.settings()}
          aria-label={ariaLabel}
          className={clsx(
            "relative grid size-10 place-items-center rounded-button border border-accent/25 bg-accent/10 text-accent transition-colors hover:bg-accent/16",
            className,
          )}
        >
          <ShieldCheck aria-hidden="true" className="size-[18px]" />
        </Link>
      </Tooltip>
    );
  }

  return (
    <Link
      to={paths.settings()}
      aria-label={ariaLabel}
      className={clsx(
        "group flex items-center gap-3 rounded-button border border-accent/20 bg-accent/[0.07] px-2.5 py-2 transition-colors hover:border-accent/35 hover:bg-accent/12",
        className,
      )}
    >
      <span className="relative grid size-8 shrink-0 place-items-center rounded-control bg-accent/14 text-accent shadow-[0_0_16px_-4px_var(--accent-glow)]">
        <ShieldCheck aria-hidden="true" className="size-[17px]" />
      </span>
      <span className="min-w-0 flex-1 leading-tight">
        <span className="block text-[13px] font-semibold text-text">{label}</span>
        <span className="block truncate text-[11.5px] text-muted">{hint}</span>
      </span>
      <ChevronRight
        aria-hidden="true"
        className="size-4 shrink-0 text-faint transition-transform duration-150 group-hover:text-muted full-motion:group-hover:translate-x-0.5"
      />
    </Link>
  );
}
