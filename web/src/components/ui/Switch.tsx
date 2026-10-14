import clsx from "clsx";
import { Switch as SwitchPrimitive } from "radix-ui";
import { useId, type ComponentPropsWithRef, type ReactNode } from "react";

export interface SwitchProps extends Omit<ComponentPropsWithRef<typeof SwitchPrimitive.Root>, "children"> {
  /** Visible label (clicking it toggles). Without it, pass `aria-label`. */
  label?: ReactNode;
  /** Help text under the label (linked with aria-describedby). */
  description?: ReactNode;
  size?: "sm" | "md";
}

/** On/off switch (Radix, role="switch"; Space/Enter toggles). */
export function Switch({ label, description, size = "md", id, className, ...rest }: SwitchProps) {
  const autoId = useId();
  const switchId = id ?? `switch-${autoId}`;
  const descriptionId = description ? `${switchId}-description` : undefined;
  const control = (
    <SwitchPrimitive.Root
      {...rest}
      id={switchId}
      aria-describedby={descriptionId ?? rest["aria-describedby"]}
      className={clsx(
        "peer relative inline-flex shrink-0 items-center rounded-pill border p-[2px] pointer-coarse:touch-target",
        "border-border-strong bg-surface-3 transition-[background-color,border-color,box-shadow] duration-200",
        "data-[state=checked]:border-transparent data-[state=checked]:bg-accent data-[state=checked]:shadow-[0_0_14px_-2px_var(--accent-glow)]",
        "disabled:cursor-not-allowed disabled:opacity-45",
        size === "md" ? "h-6 w-11" : "h-5 w-9",
        !label && className,
      )}
    >
      <SwitchPrimitive.Thumb
        className={clsx(
          "pointer-events-none block rounded-full bg-white shadow-[0_1px_3px_rgba(0,0,0,0.35)] transition-transform duration-200 ease-smooth",
          size === "md" ? "size-[18px] data-[state=checked]:translate-x-5" : "size-3.5 data-[state=checked]:translate-x-4",
        )}
      />
    </SwitchPrimitive.Root>
  );
  if (!label) return control;
  return (
    <div className={clsx("flex items-start justify-between gap-4", className)}>
      <div className="min-w-0">
        <label htmlFor={switchId} className="text-sm font-medium text-text">
          {label}
        </label>
        {description ? (
          <p id={descriptionId} className="mt-0.5 text-[13px] text-muted">
            {description}
          </p>
        ) : null}
      </div>
      {control}
    </div>
  );
}
