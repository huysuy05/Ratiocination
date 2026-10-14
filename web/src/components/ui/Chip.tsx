import clsx from "clsx";
import { X } from "lucide-react";
import type { ComponentPropsWithRef, ReactNode } from "react";
import { toneColor, type CSSVariables, type Tone } from "./tones";

export type ChipSize = "sm" | "md";

const SIZES: Readonly<Record<ChipSize, string>> = {
  sm: "h-7 gap-1.5 px-2.5 text-[12.5px] [&_svg]:size-3.5",
  md: "h-8 gap-2 px-3 text-[13px] [&_svg]:size-4",
};

const BASE =
  "inline-flex max-w-full shrink-0 items-center rounded-pill border font-medium whitespace-nowrap [&_svg]:shrink-0";

export interface ChipProps extends ComponentPropsWithRef<"span"> {
  tone?: Tone;
  /** Raw CSS colour (overrides `tone`). */
  color?: string;
  size?: ChipSize;
  icon?: ReactNode;
  variant?: "soft" | "outline" | "dashed";
  /** Adds a remove (×) button. */
  onRemove?: () => void;
  /** Accessible name of the remove button, e.g. `Remove "cs.LG"` (default "Remove"). */
  removeLabel?: string;
  removeDisabled?: boolean;
}

/** Pill for categories, topics and filters (UI.md §2: chips are full pills). */
export function Chip({
  tone = "neutral",
  color,
  size = "sm",
  icon,
  variant = "soft",
  onRemove,
  removeLabel = "Remove",
  removeDisabled = false,
  className,
  style,
  children,
  ...rest
}: ChipProps) {
  const vars: CSSVariables = { ...style, "--tone": toneColor(tone, color) };
  return (
    <span
      {...rest}
      style={vars}
      className={clsx(
        BASE,
        SIZES[size],
        variant === "soft" && "border-border bg-surface-2 text-text",
        variant === "outline" && "border-(--tone)/45 text-text",
        variant === "dashed" && "border-dashed border-(--tone)/60 text-text",
        tone !== "neutral" && variant === "soft" && "border-(--tone)/25 bg-(--tone)/10",
        onRemove && "pr-1",
        className,
      )}
    >
      {icon ? <span className="inline-flex text-(--tone)">{icon}</span> : null}
      <span className="min-w-0 truncate">{children}</span>
      {onRemove ? (
        <button
          type="button"
          aria-label={removeLabel}
          disabled={removeDisabled}
          onClick={onRemove}
          className="relative grid size-5 shrink-0 pointer-coarse:touch-target place-items-center rounded-full text-faint transition-colors hover:bg-surface-3 hover:text-text disabled:opacity-40 [&_svg]:size-3"
        >
          <X aria-hidden="true" strokeWidth={2.5} />
        </button>
      ) : null}
    </span>
  );
}

export interface ToggleChipProps extends Omit<ComponentPropsWithRef<"button">, "onChange"> {
  selected: boolean;
  onSelectedChange?: (selected: boolean) => void;
  tone?: Tone;
  color?: string;
  size?: ChipSize;
  icon?: ReactNode;
  /** Trailing count, e.g. number of edges of a relation. */
  count?: ReactNode;
}

/** Filter chip: a toggle button with `aria-pressed` (graph relation filters, library filters). */
export function ToggleChip({
  selected,
  onSelectedChange,
  tone = "accent",
  color,
  size = "sm",
  icon,
  count,
  className,
  style,
  children,
  onClick,
  ...rest
}: ToggleChipProps) {
  const vars: CSSVariables = { ...style, "--tone": toneColor(tone, color) };
  return (
    <button
      type="button"
      {...rest}
      aria-pressed={selected}
      data-selected={selected || undefined}
      style={vars}
      onClick={(event) => {
        onClick?.(event);
        if (!event.defaultPrevented) onSelectedChange?.(!selected);
      }}
      className={clsx(
        BASE,
        SIZES[size],
        "relative pointer-coarse:touch-target transition-[color,background-color,border-color,box-shadow] duration-150",
        "disabled:pointer-events-none disabled:opacity-45",
        selected
          ? "border-(--tone)/45 bg-(--tone)/14 text-text shadow-[inset_0_1px_0_rgba(255,255,255,0.06)]"
          : "border-border bg-transparent text-muted hover:border-border-strong hover:bg-surface-2 hover:text-text",
        className,
      )}
    >
      {icon ? <span className={clsx("inline-flex", selected ? "text-(--tone)" : "text-faint")}>{icon}</span> : null}
      <span className="min-w-0 truncate">{children}</span>
      {count !== undefined && count !== null ? (
        <span className="rounded-pill bg-surface-3 px-1.5 py-0.5 text-[11px] leading-none text-muted tabular-nums">{count}</span>
      ) : null}
    </button>
  );
}
