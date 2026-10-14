import clsx from "clsx";
import { Slider as SliderPrimitive } from "radix-ui";
import { useId, type ComponentPropsWithRef, type ReactNode } from "react";

export interface SliderProps
  extends Omit<
    ComponentPropsWithRef<typeof SliderPrimitive.Root>,
    "value" | "defaultValue" | "onValueChange" | "onValueCommit" | "children"
  > {
  value: number;
  onValueChange: (value: number) => void;
  /** Called when the user finishes dragging (or presses a key). */
  onValueCommit?: (value: number) => void;
  /** Accessible name of the thumb; shown above the track unless `hideLabel`. */
  label: string;
  hideLabel?: boolean;
  description?: ReactNode;
  /** Visible/spoken value, e.g. (v) => `${v} days`. */
  formatValue?: (value: number) => string;
  showValue?: boolean;
}

/** Single-value slider (Radix, role="slider"; arrows/Page keys/Home/End). */
export function Slider({
  value,
  onValueChange,
  onValueCommit,
  label,
  hideLabel = false,
  description,
  formatValue,
  showValue = true,
  className,
  disabled,
  ...rest
}: SliderProps) {
  const labelId = `slider-${useId()}`;
  const display = formatValue ? formatValue(value) : String(value);
  return (
    <div className={clsx("flex flex-col gap-2", disabled && "opacity-50", className)}>
      {!hideLabel || showValue ? (
        <div className="flex items-baseline justify-between gap-3">
          <span id={labelId} className={clsx("text-sm font-medium text-text", hideLabel && "sr-only")}>
            {label}
          </span>
          {showValue ? (
            <span aria-hidden="true" className="text-[13px] font-medium text-muted tabular-nums">
              {display}
            </span>
          ) : null}
        </div>
      ) : null}
      <SliderPrimitive.Root
        {...rest}
        disabled={disabled}
        value={[value]}
        onValueChange={(values) => {
          const next = values[0];
          if (next !== undefined) onValueChange(next);
        }}
        onValueCommit={(values) => {
          const next = values[0];
          if (next !== undefined) onValueCommit?.(next);
        }}
        className="relative flex h-6 w-full touch-none items-center select-none pointer-coarse:h-11"
      >
        <SliderPrimitive.Track className="relative h-1.5 grow overflow-hidden rounded-pill bg-surface-3">
          <SliderPrimitive.Range className="absolute h-full rounded-pill bg-accent shadow-[0_0_10px_var(--accent-glow)]" />
        </SliderPrimitive.Track>
        <SliderPrimitive.Thumb
          aria-label={label}
          aria-valuetext={display}
          className={clsx(
            "block size-5 rounded-full border-2 border-accent bg-surface shadow-[0_2px_6px_rgba(0,0,0,0.3)]",
            "transition-[box-shadow,transform] duration-150 hover:shadow-[0_0_0_6px_var(--accent-glow)] full-motion:active:scale-110",
            "focus-visible:shadow-[0_0_0_6px_var(--accent-glow)] disabled:pointer-events-none",
          )}
        />
      </SliderPrimitive.Root>
      {description ? <p className="text-[12.5px] text-muted">{description}</p> : null}
    </div>
  );
}
