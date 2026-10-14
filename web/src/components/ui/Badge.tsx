import clsx from "clsx";
import type { ComponentPropsWithRef, ReactNode } from "react";
import { toneColor, type CSSVariables, type Tone } from "./tones";

export type BadgeVariant = "soft" | "solid" | "outline" | "dashed";
export type BadgeSize = "sm" | "md";

const SIZES: Readonly<Record<BadgeSize, string>> = {
  sm: "h-6 gap-1 px-2 text-[11.5px] [&_svg]:size-3",
  md: "h-7 gap-1.5 px-2.5 text-[12.5px] [&_svg]:size-3.5",
};

/* Text is pulled toward --text so tinted badges keep ≥ 4.5:1 in both themes. */
const TINTED_TEXT = "text-[color-mix(in_oklab,var(--tone)_80%,var(--text))]";

const VARIANTS: Readonly<Record<BadgeVariant, string>> = {
  soft: clsx("border border-(--tone)/25 bg-(--tone)/12", TINTED_TEXT),
  solid: "border border-transparent bg-(--tone) text-on-accent",
  outline: clsx("border border-(--tone)/45", TINTED_TEXT),
  dashed: clsx("border border-dashed border-(--tone)/60", TINTED_TEXT),
};

export interface BadgeProps extends ComponentPropsWithRef<"span"> {
  tone?: Tone;
  /** Raw CSS colour (overrides `tone`), e.g. `STATE_META.saved.color`. */
  color?: string;
  variant?: BadgeVariant;
  size?: BadgeSize;
  icon?: ReactNode;
  /** One soft glow pulse on mount (the "High confidence" badge, UI.md §6). */
  glow?: boolean;
}

/** Non-interactive status label (pill). Pair colour with an icon or text — never colour alone. */
export function Badge({
  tone = "neutral",
  color,
  variant = "soft",
  size = "sm",
  icon,
  glow = false,
  className,
  style,
  children,
  ...rest
}: BadgeProps) {
  const vars: CSSVariables = { ...style, "--tone": toneColor(tone, color) };
  return (
    <span
      {...rest}
      style={vars}
      className={clsx(
        "inline-flex max-w-full shrink-0 items-center rounded-pill leading-none font-medium whitespace-nowrap [&_svg]:shrink-0",
        SIZES[size],
        VARIANTS[variant],
        glow && "animate-glow-once",
        className,
      )}
    >
      {icon}
      {children}
    </span>
  );
}
