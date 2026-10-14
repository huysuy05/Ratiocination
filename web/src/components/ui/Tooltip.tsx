import clsx from "clsx";
import { Tooltip as TooltipPrimitive } from "radix-ui";
import { createContext, useContext, type ReactElement, type ReactNode } from "react";
import { Kbd } from "./Kbd";

const TooltipProviderContext = createContext(false);

export interface TooltipProviderProps {
  children?: ReactNode;
  /** Hover delay before the first tooltip opens (ms). */
  delayDuration?: number;
  /** Window in which moving to another trigger opens its tooltip instantly (ms). */
  skipDelayDuration?: number;
}

/**
 * Shares tooltip delays across the app (the AppShell mounts one). A `Tooltip` rendered without a
 * provider creates its own, so components work in isolation and in tests.
 */
export function TooltipProvider({ children, delayDuration = 300, skipDelayDuration = 300 }: TooltipProviderProps) {
  return (
    <TooltipProviderContext.Provider value={true}>
      <TooltipPrimitive.Provider delayDuration={delayDuration} skipDelayDuration={skipDelayDuration}>
        {children}
      </TooltipPrimitive.Provider>
    </TooltipProviderContext.Provider>
  );
}

export type TooltipSide = "top" | "right" | "bottom" | "left";

export interface TooltipProps {
  /** Tooltip text. Empty/null renders the child alone. */
  content: ReactNode;
  /** Keyboard shortcut shown as a keycap, e.g. "Z" (use `shortcutKey` for "ArrowLeft"-style names). */
  shortcut?: string;
  /** A `KeyboardEvent.key` value rendered as its glyph ("ArrowLeft" → "←"). */
  shortcutKey?: string;
  side?: TooltipSide;
  align?: "start" | "center" | "end";
  sideOffset?: number;
  /** Render the child without a tooltip. */
  disabled?: boolean;
  open?: boolean;
  defaultOpen?: boolean;
  onOpenChange?: (open: boolean) => void;
  delayDuration?: number;
  className?: string;
  /** The trigger: one element that accepts a ref and DOM props (a button, a link, …). */
  children: ReactElement;
}

/**
 * Accessible tooltip (Radix): opens on hover and keyboard focus, closes on Escape. Tooltips are
 * supplementary — the trigger keeps its own accessible name (touch devices never show them).
 */
export function Tooltip({
  content,
  shortcut,
  shortcutKey,
  side = "top",
  align = "center",
  sideOffset = 8,
  disabled = false,
  open,
  defaultOpen,
  onOpenChange,
  delayDuration,
  className,
  children,
}: TooltipProps) {
  const hasProvider = useContext(TooltipProviderContext);
  if (disabled || content === null || content === undefined || content === false || content === "") return children;

  const tooltip = (
    <TooltipPrimitive.Root open={open} defaultOpen={defaultOpen} onOpenChange={onOpenChange} delayDuration={delayDuration}>
      <TooltipPrimitive.Trigger asChild>{children}</TooltipPrimitive.Trigger>
      <TooltipPrimitive.Portal>
        <TooltipPrimitive.Content
          side={side}
          align={align}
          sideOffset={sideOffset}
          collisionPadding={8}
          className={clsx(
            "z-[70] flex max-w-xs origin-(--radix-tooltip-content-transform-origin) items-center gap-2 select-none",
            "rounded-[9px] border px-2.5 py-1.5 text-[12.5px] leading-snug font-medium elevation-popover",
            "border-transparent bg-text text-surface dark:border-border-strong dark:bg-surface-3 dark:text-text",
            "data-[state=closed]:animate-float-out data-[state=delayed-open]:animate-float-in data-[state=instant-open]:animate-float-in",
            className,
          )}
        >
          <span>{content}</span>
          {shortcut || shortcutKey ? (
            <Kbd
              keyName={shortcutKey}
              className="border-white/20 bg-white/10 text-surface/80 shadow-none dark:border-border-strong dark:bg-surface-2 dark:text-muted dark:shadow-[inset_0_-1px_0_var(--border-strong)]"
            >
              {shortcut}
            </Kbd>
          ) : null}
        </TooltipPrimitive.Content>
      </TooltipPrimitive.Portal>
    </TooltipPrimitive.Root>
  );

  return hasProvider ? tooltip : <TooltipPrimitive.Provider delayDuration={300}>{tooltip}</TooltipPrimitive.Provider>;
}
