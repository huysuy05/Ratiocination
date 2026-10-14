import clsx from "clsx";
import { TriangleAlert } from "lucide-react";
import { AlertDialog } from "radix-ui";
import { useEffect, useRef, useState, type ReactElement, type ReactNode } from "react";
import { Button } from "./Button";
import { DIALOG_SURFACE_CLASS, DialogFooter, OVERLAY_CLASS } from "./Dialog";

export interface ConfirmDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title: ReactNode;
  description?: ReactNode;
  /** Extra details, e.g. the list of what gets deleted. */
  children?: ReactNode;
  confirmLabel?: ReactNode;
  cancelLabel?: ReactNode;
  /** "danger": red confirm button, warning icon, and Cancel focused first. */
  tone?: "default" | "danger";
  /**
   * Called on confirm. A returned promise keeps the dialog open with a busy button until it
   * settles: it closes on success and stays open on rejection (report the error yourself).
   */
  onConfirm: () => void | Promise<unknown>;
  /** External busy state (e.g. a mutation's isPending). */
  pending?: boolean;
  /** Disable the confirm button (e.g. until a typed confirmation matches). */
  confirmDisabled?: boolean;
  icon?: ReactNode;
  trigger?: ReactElement;
}

function isPromiseLike(value: unknown): value is PromiseLike<unknown> {
  return typeof value === "object" && value !== null && typeof (value as { then?: unknown }).then === "function";
}

/** Confirmation dialog (Radix AlertDialog, role="alertdialog"; UI.md §12). */
export function ConfirmDialog({
  open,
  onOpenChange,
  title,
  description,
  children,
  confirmLabel = "Confirm",
  cancelLabel = "Cancel",
  tone = "default",
  onConfirm,
  pending = false,
  confirmDisabled = false,
  icon,
  trigger,
}: ConfirmDialogProps) {
  const [busy, setBusy] = useState(false);
  const mounted = useRef(true);
  const confirmRef = useRef<HTMLButtonElement>(null);
  const isPending = pending || busy;
  const danger = tone === "danger";
  const hasDescription = description !== undefined && description !== null && description !== false;

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);

  const confirm = async () => {
    if (isPending) return;
    let result: unknown;
    try {
      result = onConfirm();
    } catch {
      return;
    }
    if (!isPromiseLike(result)) {
      onOpenChange(false);
      return;
    }
    setBusy(true);
    try {
      await result;
      if (mounted.current) onOpenChange(false);
    } catch {
      // Stay open; the caller reports the error.
    } finally {
      if (mounted.current) setBusy(false);
    }
  };

  return (
    <AlertDialog.Root
      open={open}
      onOpenChange={(next) => {
        if (!next && isPending) return;
        onOpenChange(next);
      }}
    >
      {trigger ? <AlertDialog.Trigger asChild>{trigger}</AlertDialog.Trigger> : null}
      <AlertDialog.Portal>
        <AlertDialog.Overlay className={OVERLAY_CLASS}>
          <AlertDialog.Content
            {...(hasDescription ? {} : { "aria-describedby": undefined })}
            onOpenAutoFocus={
              danger
                ? undefined
                : (event) => {
                    event.preventDefault();
                    confirmRef.current?.focus();
                  }
            }
            onEscapeKeyDown={(event) => {
              if (isPending) event.preventDefault();
            }}
            className={clsx(DIALOG_SURFACE_CLASS, "max-w-[440px] p-6")}
          >
            <div className="flex items-start gap-4">
              {danger || icon ? (
                <div
                  className={clsx(
                    "grid size-10 shrink-0 place-items-center rounded-full ring-1 [&_svg]:size-5",
                    danger ? "bg-not-relevant/12 text-not-relevant ring-not-relevant/25" : "bg-accent/12 text-accent ring-accent/25",
                  )}
                >
                  {icon ?? <TriangleAlert aria-hidden="true" />}
                </div>
              ) : null}
              <div className="min-w-0 flex-1 pt-1">
                <AlertDialog.Title className="text-[17px] leading-snug font-semibold tracking-[-0.012em]">{title}</AlertDialog.Title>
                {hasDescription ? (
                  <AlertDialog.Description className="mt-1.5 text-sm text-muted">{description}</AlertDialog.Description>
                ) : null}
                {children ? <div className="mt-4 text-sm text-text">{children}</div> : null}
              </div>
            </div>
            <DialogFooter>
              <AlertDialog.Cancel asChild>
                <Button variant="ghost" disabled={isPending}>
                  {cancelLabel}
                </Button>
              </AlertDialog.Cancel>
              <Button
                ref={confirmRef}
                variant={danger ? "danger" : "primary"}
                loading={isPending}
                disabled={confirmDisabled}
                onClick={() => {
                  void confirm();
                }}
              >
                {confirmLabel}
              </Button>
            </DialogFooter>
          </AlertDialog.Content>
        </AlertDialog.Overlay>
      </AlertDialog.Portal>
    </AlertDialog.Root>
  );
}
