import clsx from "clsx";
import { Slot } from "radix-ui";
import type { ComponentPropsWithRef, MouseEvent, ReactNode } from "react";
import { Spinner } from "./Spinner";

export type ButtonVariant = "primary" | "secondary" | "outline" | "ghost" | "danger" | "danger-soft";
export type ButtonSize = "sm" | "md" | "lg";

const BASE =
  "relative inline-flex shrink-0 select-none items-center justify-center gap-2 whitespace-nowrap rounded-button " +
  "font-medium tracking-[-0.006em] transition-[color,background-color,border-color,box-shadow,transform,opacity,filter] " +
  "duration-150 ease-smooth disabled:pointer-events-none disabled:opacity-45 aria-disabled:pointer-events-none " +
  "full-motion:active:scale-[0.97] [&_svg]:pointer-events-none [&_svg]:shrink-0";

const SIZES: Readonly<Record<ButtonSize, string>> = {
  sm: "h-8 px-3 text-[13px] [&_svg]:size-3.5 pointer-coarse:h-11 pointer-coarse:px-4",
  md: "h-10 px-4 text-sm [&_svg]:size-4 pointer-coarse:h-11",
  lg: "h-12 px-5 text-[15px] [&_svg]:size-[18px]",
};

const VARIANTS: Readonly<Record<ButtonVariant, string>> = {
  primary:
    "bg-accent text-on-accent font-semibold shadow-[inset_0_1px_0_rgba(255,255,255,0.22),0_8px_22px_-10px_var(--accent-glow)] " +
    "hover:bg-accent-hover hover:shadow-[inset_0_1px_0_rgba(255,255,255,0.28),0_10px_28px_-8px_var(--accent-glow)]",
  secondary:
    "border border-border bg-surface-2 text-text shadow-[var(--inner-highlight)] hover:border-border-strong hover:bg-surface-3",
  outline: "border border-border-strong bg-transparent text-text hover:bg-surface-2",
  ghost: "text-muted hover:bg-surface-2 hover:text-text",
  danger:
    "bg-not-relevant text-on-accent font-semibold shadow-[inset_0_1px_0_rgba(255,255,255,0.18),0_8px_22px_-12px_var(--not-relevant)] hover:brightness-110",
  "danger-soft": "border border-not-relevant/30 bg-not-relevant/10 text-not-relevant hover:bg-not-relevant/16",
};

export interface ButtonStyleOptions {
  variant?: ButtonVariant;
  size?: ButtonSize;
  fullWidth?: boolean;
  className?: string;
}

/** Class names of a Button, for elements that cannot use `asChild`. */
export function buttonClassName({ variant = "secondary", size = "md", fullWidth = false, className }: ButtonStyleOptions = {}): string {
  return clsx(BASE, SIZES[size], VARIANTS[variant], fullWidth && "w-full", className);
}

export interface ButtonProps extends ComponentPropsWithRef<"button"> {
  variant?: ButtonVariant;
  size?: ButtonSize;
  /** Shows a spinner and blocks clicks while keeping focus (aria-busy, aria-disabled). */
  loading?: boolean;
  leadingIcon?: ReactNode;
  trailingIcon?: ReactNode;
  /** Render the single child element (e.g. a router `<Link>`) with button styling. */
  asChild?: boolean;
  fullWidth?: boolean;
}

/**
 * Button (UI.md §12). Variants: primary (accent + `--on-accent`), secondary, outline, ghost, danger,
 * danger-soft. `type` defaults to "button". Touch targets grow to 44 px on coarse pointers.
 */
export function Button({
  variant = "secondary",
  size = "md",
  loading = false,
  leadingIcon,
  trailingIcon,
  asChild = false,
  fullWidth = false,
  className,
  children,
  type,
  onClick,
  "aria-disabled": ariaDisabled,
  ...rest
}: ButtonProps) {
  const classes = buttonClassName({ variant, size, fullWidth, className });

  if (asChild) {
    return (
      <Slot.Root {...rest} aria-disabled={ariaDisabled} className={classes} onClick={onClick}>
        {leadingIcon}
        <Slot.Slottable>{children}</Slot.Slottable>
        {trailingIcon}
      </Slot.Root>
    );
  }

  const handleClick = loading
    ? (event: MouseEvent<HTMLButtonElement>) => {
        event.preventDefault();
      }
    : onClick;

  return (
    <button
      {...rest}
      type={type ?? "button"}
      className={classes}
      aria-busy={loading || undefined}
      aria-disabled={loading ? true : ariaDisabled}
      data-loading={loading || undefined}
      onClick={handleClick}
    >
      {loading ? <Spinner /> : leadingIcon}
      {children}
      {trailingIcon}
    </button>
  );
}
