import clsx from "clsx";
import { X } from "lucide-react";
import { Dialog as DialogPrimitive } from "radix-ui";
import { useRef, type ComponentProps, type ReactElement, type ReactNode } from "react";
import { IconButton } from "./IconButton";

type ContentProps = ComponentProps<typeof DialogPrimitive.Content>;

export type SheetSide = "right" | "left" | "bottom";
export type SheetSize = "sm" | "md" | "lg";

/* Desktop widths; < 768 px every sheet is full-screen (UI.md §4). md = min(640 px, 92vw) (UI.md §7). */
const WIDTHS: Readonly<Record<SheetSize, string>> = {
  sm: "md:w-[min(420px,92vw)]",
  md: "md:w-[min(640px,92vw)]",
  lg: "md:w-[min(880px,92vw)]",
};

const SIDES: Readonly<Record<SheetSide, string>> = {
  right:
    "md:inset-y-3 md:right-3 md:left-auto md:data-[state=open]:animate-sheet-in-right md:data-[state=closed]:animate-sheet-out-right",
  left: "md:inset-y-3 md:left-3 md:right-auto md:data-[state=open]:animate-sheet-in-left md:data-[state=closed]:animate-sheet-out-left",
  bottom: "md:inset-x-3 md:top-auto md:bottom-3 md:max-h-[85dvh] md:w-auto!",
};

export interface SheetProps {
  open?: boolean;
  defaultOpen?: boolean;
  onOpenChange?: (open: boolean) => void;
  trigger?: ReactElement;
  title: ReactNode;
  description?: ReactNode;
  /** Keep the title/description for assistive tech only (render your own header in children). */
  hideTitle?: boolean;
  /** Extra header controls, left of the close button. */
  headerActions?: ReactNode;
  children?: ReactNode;
  /** Sticky footer (e.g. the deck actions in the paper sheet). */
  footer?: ReactNode;
  side?: SheetSide;
  size?: SheetSize;
  showClose?: boolean;
  className?: string;
  bodyClassName?: string;
  onOpenAutoFocus?: ContentProps["onOpenAutoFocus"];
  onCloseAutoFocus?: ContentProps["onCloseAutoFocus"];
  onEscapeKeyDown?: ContentProps["onEscapeKeyDown"];
  onInteractOutside?: ContentProps["onInteractOutside"];
}

/**
 * Side sheet (Radix Dialog): a floating panel sliding in from the side on ≥ 768 px, full-screen
 * sliding up on phones. Focus is trapped; Escape, the × button and an outside click close it.
 * On open, focus goes to the panel itself (not the × button, whose tooltip would otherwise open
 * and swallow the first Escape); pass `onOpenAutoFocus` and call `preventDefault()` to override.
 */
export function Sheet({
  open,
  defaultOpen,
  onOpenChange,
  trigger,
  title,
  description,
  hideTitle = false,
  headerActions,
  children,
  footer,
  side = "right",
  size = "md",
  showClose = true,
  className,
  bodyClassName,
  onOpenAutoFocus,
  onCloseAutoFocus,
  onEscapeKeyDown,
  onInteractOutside,
}: SheetProps) {
  const hasDescription = description !== undefined && description !== null && description !== false;
  const showHeader = !hideTitle || headerActions !== undefined;
  const floatingClose = showClose && !showHeader;
  const contentRef = useRef<HTMLDivElement>(null);
  const focusPanel: ContentProps["onOpenAutoFocus"] = (event) => {
    onOpenAutoFocus?.(event);
    if (event.defaultPrevented) return;
    event.preventDefault();
    contentRef.current?.focus({ preventScroll: true });
  };
  return (
    <DialogPrimitive.Root open={open} defaultOpen={defaultOpen} onOpenChange={onOpenChange}>
      {trigger ? <DialogPrimitive.Trigger asChild>{trigger}</DialogPrimitive.Trigger> : null}
      <DialogPrimitive.Portal>
        <DialogPrimitive.Overlay className="fixed inset-0 z-50 bg-overlay backdrop-blur-[2px] data-[state=closed]:animate-fade-out data-[state=open]:animate-fade-in" />
        <DialogPrimitive.Content
          ref={contentRef}
          {...(hasDescription ? {} : { "aria-describedby": undefined })}
          onOpenAutoFocus={focusPanel}
          onCloseAutoFocus={onCloseAutoFocus}
          onEscapeKeyDown={onEscapeKeyDown}
          onInteractOutside={onInteractOutside}
          data-side={side}
          className={clsx(
            "fixed inset-0 z-50 flex flex-col bg-surface text-text outline-none",
            "data-[state=closed]:animate-sheet-out-bottom data-[state=open]:animate-sheet-in-bottom",
            "md:rounded-panel md:border md:border-border md:surface-sheen md:elevation-overlay",
            WIDTHS[size],
            SIDES[side],
            className,
          )}
        >
          {showHeader ? (
            <header
              className={clsx(
                "flex shrink-0 items-start gap-3 border-b border-border px-5 pt-[max(1rem,env(safe-area-inset-top))] pb-4 md:px-6 md:pt-5",
                hideTitle && "border-b-0 pb-0",
              )}
            >
              <div className={clsx("min-w-0 flex-1", hideTitle && "sr-only")}>
                <DialogPrimitive.Title className="text-[17px] leading-snug font-semibold tracking-[-0.012em]">{title}</DialogPrimitive.Title>
                {hasDescription ? (
                  <DialogPrimitive.Description className="mt-1 text-sm text-muted">{description}</DialogPrimitive.Description>
                ) : null}
              </div>
              {hideTitle ? <div className="flex-1" /> : null}
              {headerActions ? <div className="flex shrink-0 items-center gap-1">{headerActions}</div> : null}
              {showClose ? (
                <DialogPrimitive.Close asChild>
                  <IconButton label="Close" icon={<X />} size="sm" shortcutKey="Escape" tooltipSide="bottom" className="-mt-1 -mr-2" />
                </DialogPrimitive.Close>
              ) : null}
            </header>
          ) : (
            <div className="sr-only">
              <DialogPrimitive.Title>{title}</DialogPrimitive.Title>
              {hasDescription ? <DialogPrimitive.Description>{description}</DialogPrimitive.Description> : null}
            </div>
          )}
          {floatingClose ? (
            // Positioned wrapper: IconButton is `relative`, which would override an `absolute` class.
            <div className="absolute top-[max(0.75rem,env(safe-area-inset-top))] right-3 z-10">
              <DialogPrimitive.Close asChild>
                <IconButton label="Close" icon={<X />} size="sm" shortcutKey="Escape" tooltipSide="bottom" className="bg-surface/80 backdrop-blur" />
              </DialogPrimitive.Close>
            </div>
          ) : null}
          <div className={clsx("min-h-0 flex-1 overflow-y-auto overscroll-contain px-5 py-5 md:px-6", bodyClassName)}>{children}</div>
          {footer ? (
            <footer className="shrink-0 border-t border-border bg-surface px-5 pt-3 pb-[max(0.75rem,env(safe-area-inset-bottom))] md:rounded-b-panel md:px-6">
              {footer}
            </footer>
          ) : null}
        </DialogPrimitive.Content>
      </DialogPrimitive.Portal>
    </DialogPrimitive.Root>
  );
}

/** Closes the surrounding Sheet: `<SheetClose asChild><Button>Done</Button></SheetClose>`. */
export const SheetClose = DialogPrimitive.Close;
