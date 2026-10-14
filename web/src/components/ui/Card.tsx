import clsx from "clsx";
import { Slot } from "radix-ui";
import type { ComponentPropsWithRef } from "react";

export type CardVariant = "default" | "raised" | "inset" | "outline" | "ghost";
export type CardPadding = "none" | "sm" | "md" | "lg";

const VARIANTS: Readonly<Record<CardVariant, string>> = {
  default: "border border-border bg-surface surface-sheen elevation-card",
  raised: "border border-border bg-surface surface-sheen elevation-raised",
  inset: "border border-border bg-surface-2",
  outline: "border border-border-strong",
  ghost: "",
};

const PADDINGS: Readonly<Record<CardPadding, string>> = {
  none: "",
  sm: "p-4",
  md: "p-5 md:p-6",
  lg: "p-6 md:p-8",
};

export interface CardProps extends ComponentPropsWithRef<"div"> {
  variant?: CardVariant;
  padding?: CardPadding;
  /** "card" = 20 px (paper cards), "panel" = 16 px (panels, list rows). */
  radius?: "card" | "panel";
  /** Hover lift + stronger border (use for clickable cards; pair with asChild + a link/button). */
  interactive?: boolean;
  /** Render the child element (e.g. a `<Link>`) as the card. */
  asChild?: boolean;
}

/** Surface container (UI.md §2: cards radius 20 px, panels 16 px, dark shadow + inner highlight). */
export function Card({
  variant = "default",
  padding = "md",
  radius = "card",
  interactive = false,
  asChild = false,
  className,
  ...rest
}: CardProps) {
  const Comp = asChild ? Slot.Root : "div";
  return (
    <Comp
      {...rest}
      className={clsx(
        "relative text-text",
        radius === "card" ? "rounded-card" : "rounded-panel",
        VARIANTS[variant],
        PADDINGS[padding],
        interactive &&
          "block transition-[border-color,box-shadow,transform,background-color] duration-200 ease-smooth hover:border-border-strong hover:elevation-raised full-motion:hover:-translate-y-0.5",
        className,
      )}
    />
  );
}

export function CardHeader({ className, ...rest }: ComponentPropsWithRef<"div">) {
  return <div {...rest} className={clsx("flex items-start justify-between gap-4", className)} />;
}

export interface CardTitleProps extends ComponentPropsWithRef<"h3"> {
  as?: "h2" | "h3" | "h4";
}

export function CardTitle({ as: Heading = "h3", className, ...rest }: CardTitleProps) {
  return <Heading {...rest} className={clsx("text-[15px] leading-snug font-semibold tracking-[-0.01em] text-text", className)} />;
}

export function CardDescription({ className, ...rest }: ComponentPropsWithRef<"p">) {
  return <p {...rest} className={clsx("text-sm text-muted", className)} />;
}

export function CardFooter({ className, ...rest }: ComponentPropsWithRef<"div">) {
  return <div {...rest} className={clsx("flex flex-wrap items-center gap-2 pt-4", className)} />;
}
