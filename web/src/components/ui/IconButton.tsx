import clsx from "clsx";
import { Slot } from "radix-ui";
import type { ComponentPropsWithRef, MouseEvent, ReactElement, ReactNode } from "react";
import { Spinner } from "./Spinner";
import { Tooltip, type TooltipSide } from "./Tooltip";

export type IconButtonVariant = "ghost" | "secondary" | "outline" | "primary" | "danger-soft";
export type IconButtonSize = "sm" | "md" | "lg";

const BASE =
  "relative inline-flex shrink-0 select-none items-center justify-center transition-[color,background-color,border-color,box-shadow,transform] " +
  "duration-150 ease-smooth disabled:pointer-events-none disabled:opacity-45 aria-disabled:pointer-events-none " +
  "full-motion:active:scale-[0.92] [&_svg]:pointer-events-none [&_svg]:shrink-0";

const SIZES: Readonly<Record<IconButtonSize, string>> = {
  sm: "size-8 [&_svg]:size-4 pointer-coarse:size-11",
  md: "size-10 [&_svg]:size-[18px] pointer-coarse:size-11",
  lg: "size-12 [&_svg]:size-5",
};

const VARIANTS: Readonly<Record<IconButtonVariant, string>> = {
  ghost: "text-muted hover:bg-surface-2 hover:text-text",
  secondary: "border border-border bg-surface-2 text-text shadow-[var(--inner-highlight)] hover:border-border-strong hover:bg-surface-3",
  outline: "border border-border-strong text-text hover:bg-surface-2",
  primary: "bg-accent text-on-accent shadow-[0_8px_22px_-10px_var(--accent-glow)] hover:bg-accent-hover",
  "danger-soft": "border border-not-relevant/30 bg-not-relevant/10 text-not-relevant hover:bg-not-relevant/16",
};

export interface IconButtonProps extends Omit<ComponentPropsWithRef<"button">, "children" | "aria-label"> {
  /** Accessible name; also the tooltip text. */
  label: string;
  /** The icon (decorative; e.g. `<X />`). */
  icon: ReactNode;
  variant?: IconButtonVariant;
  size?: IconButtonSize;
  shape?: "square" | "circle";
  /** Show the label as a tooltip on hover/focus (default true). */
  tooltip?: boolean;
  tooltipSide?: TooltipSide;
  /** Shortcut shown in the tooltip, e.g. "Z". */
  shortcut?: string;
  /** A `KeyboardEvent.key` shown as a glyph in the tooltip, e.g. "ArrowLeft". */
  shortcutKey?: string;
  loading?: boolean;
  /** Render the single child element (e.g. a `<Link>`) as the icon button. */
  asChild?: boolean;
  children?: ReactElement;
}

/** Icon-only button with a required accessible label and an optional tooltip (UI.md §12). */
export function IconButton({
  label,
  icon,
  variant = "ghost",
  size = "md",
  shape = "square",
  tooltip = true,
  tooltipSide = "top",
  shortcut,
  shortcutKey,
  loading = false,
  asChild = false,
  className,
  children,
  type,
  onClick,
  "aria-disabled": ariaDisabled,
  ...rest
}: IconButtonProps) {
  const classes = clsx(
    BASE,
    SIZES[size],
    VARIANTS[variant],
    shape === "circle" ? "rounded-full" : size === "sm" ? "rounded-control" : "rounded-button",
    className,
  );
  const content = loading ? <Spinner /> : icon;

  const control = asChild ? (
    <Slot.Root {...rest} aria-label={label} aria-disabled={ariaDisabled} className={classes} onClick={onClick}>
      <Slot.Slottable>{children}</Slot.Slottable>
      {content}
    </Slot.Root>
  ) : (
    <button
      {...rest}
      type={type ?? "button"}
      aria-label={label}
      aria-busy={loading || undefined}
      aria-disabled={loading ? true : ariaDisabled}
      className={classes}
      onClick={
        loading
          ? (event: MouseEvent<HTMLButtonElement>) => {
              event.preventDefault();
            }
          : onClick
      }
    >
      {content}
    </button>
  );

  return (
    <Tooltip content={label} shortcut={shortcut} shortcutKey={shortcutKey} side={tooltipSide} disabled={!tooltip}>
      {control}
    </Tooltip>
  );
}
