import clsx from "clsx";
import { X } from "lucide-react";
import { Dialog as DialogPrimitive } from "radix-ui";
import type { ComponentProps, ComponentPropsWithRef, ReactElement, ReactNode } from "react";
import { IconButton } from "./IconButton";

type ContentProps = ComponentProps<typeof DialogPrimitive.Content>;

export type DialogSize = "sm" | "md" | "lg";

const SIZES: Readonly<Record<DialogSize, string>> = {
  sm: "max-w-[400px]",
  md: "max-w-[480px]",
  lg: "max-w-[640px]",
};

/** Overlay that also centres and scrolls its content (shared by Dialog and ConfirmDialog). */
export const OVERLAY_CLASS =
  "fixed inset-0 z-50 grid place-items-center overflow-y-auto bg-overlay p-4 backdrop-blur-[3px] sm:p-6 " +
  "data-[state=closed]:animate-fade-out data-[state=open]:animate-fade-in";

/** Centered surface (shared by Dialog and ConfirmDialog). */
export const DIALOG_SURFACE_CLASS =
  "relative w-full rounded-card border border-border bg-surface surface-sheen text-text elevation-overlay outline-none " +
  "data-[state=closed]:animate-pop-out data-[state=open]:animate-pop-in";

export interface DialogProps {
  open?: boolean;
  defaultOpen?: boolean;
  onOpenChange?: (open: boolean) => void;
  /** Optional trigger element (opens the dialog; focus returns to it on close). */
  trigger?: ReactElement;
  title: ReactNode;
  description?: ReactNode;
  /** Keep the title for assistive tech only. */
  hideTitle?: boolean;
  /** Icon shown in a tinted circle next to the title. */
  icon?: ReactNode;
  children?: ReactNode;
  /** Action row (right-aligned on desktop, stacked on mobile). Or use `DialogFooter` inside children. */
  footer?: ReactNode;
  size?: DialogSize;
  /** Show the × button (default true). */
  showClose?: boolean;
  className?: string;
  onOpenAutoFocus?: ContentProps["onOpenAutoFocus"];
  onCloseAutoFocus?: ContentProps["onCloseAutoFocus"];
  onEscapeKeyDown?: ContentProps["onEscapeKeyDown"];
  onInteractOutside?: ContentProps["onInteractOutside"];
}

/** Modal dialog (Radix): focus trap, Escape/outside click to close, focus returns to the trigger. */
export function Dialog({
  open,
  defaultOpen,
  onOpenChange,
  trigger,
  title,
  description,
  hideTitle = false,
  icon,
  children,
  footer,
  size = "md",
  showClose = true,
  className,
  onOpenAutoFocus,
  onCloseAutoFocus,
  onEscapeKeyDown,
  onInteractOutside,
}: DialogProps) {
  const hasDescription = description !== undefined && description !== null && description !== false;
  return (
    <DialogPrimitive.Root open={open} defaultOpen={defaultOpen} onOpenChange={onOpenChange}>
      {trigger ? <DialogPrimitive.Trigger asChild>{trigger}</DialogPrimitive.Trigger> : null}
      <DialogPrimitive.Portal>
        <DialogPrimitive.Overlay className={OVERLAY_CLASS}>
          <DialogPrimitive.Content
            {...(hasDescription ? {} : { "aria-describedby": undefined })}
            onOpenAutoFocus={onOpenAutoFocus}
            onCloseAutoFocus={onCloseAutoFocus}
            onEscapeKeyDown={onEscapeKeyDown}
            onInteractOutside={onInteractOutside}
            className={clsx(DIALOG_SURFACE_CLASS, SIZES[size], className)}
          >
            <div className={clsx("flex items-start gap-4 px-6 pt-6", hideTitle && !icon && "sr-only")}>
              {icon ? (
                <div className="grid size-10 shrink-0 place-items-center rounded-full bg-accent/12 text-accent ring-1 ring-accent/25 [&_svg]:size-5">
                  {icon}
                </div>
              ) : null}
              <div className={clsx("min-w-0 flex-1 pt-1", showClose && "pr-8")}>
                <DialogPrimitive.Title className={clsx("text-[17px] leading-snug font-semibold tracking-[-0.012em]", hideTitle && "sr-only")}>
                  {title}
                </DialogPrimitive.Title>
                {hasDescription ? (
                  <DialogPrimitive.Description className="mt-1.5 text-sm text-muted">{description}</DialogPrimitive.Description>
                ) : null}
              </div>
            </div>
            {showClose ? (
              // Positioned wrapper: IconButton is `relative`, which would override an `absolute` class.
              <div className="absolute top-4 right-4">
                <DialogPrimitive.Close asChild>
                  <IconButton label="Close" icon={<X />} size="sm" tooltip={false} />
                </DialogPrimitive.Close>
              </div>
            ) : null}
            <div className={clsx("px-6 pb-6", children ? "pt-5" : "pt-0")}>
              {children}
              {footer ? <DialogFooter>{footer}</DialogFooter> : null}
            </div>
          </DialogPrimitive.Content>
        </DialogPrimitive.Overlay>
      </DialogPrimitive.Portal>
    </DialogPrimitive.Root>
  );
}

/** Action row for dialogs: stacked (primary on top) on mobile, right-aligned on larger screens. */
export function DialogFooter({ className, ...rest }: ComponentPropsWithRef<"div">) {
  return <div {...rest} className={clsx("mt-6 flex flex-col-reverse gap-2 sm:flex-row sm:items-center sm:justify-end", className)} />;
}

/** Closes the surrounding Dialog/Sheet: `<DialogClose asChild><Button>Cancel</Button></DialogClose>`. */
export const DialogClose = DialogPrimitive.Close;
