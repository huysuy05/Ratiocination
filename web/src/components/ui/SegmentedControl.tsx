import clsx from "clsx";
import { motion } from "motion/react";
import { RadioGroup } from "radix-ui";
import { useId, type ReactNode } from "react";
import { SPRING } from "../../lib/motion";
import { Tooltip } from "./Tooltip";

export interface SegmentedOption<T extends string> {
  value: T;
  label: string;
  icon?: ReactNode;
  disabled?: boolean;
}

export interface SegmentedControlProps<T extends string> {
  /** Accessible name of the group, e.g. "Theme". */
  label: string;
  value: T;
  onValueChange: (value: T) => void;
  options: readonly SegmentedOption<T>[];
  /** Show icons only (labels become accessible names + tooltips). */
  iconOnly?: boolean;
  size?: "sm" | "md";
  fullWidth?: boolean;
  className?: string;
}

/**
 * Exclusive choice as a segmented control (Radix RadioGroup: role="radiogroup", arrow keys move and
 * select). The selected segment has an animated thumb.
 */
export function SegmentedControl<T extends string>({
  label,
  value,
  onValueChange,
  options,
  iconOnly = false,
  size = "md",
  fullWidth = false,
  className,
}: SegmentedControlProps<T>) {
  const thumbId = `segmented-${useId()}`;
  return (
    <RadioGroup.Root
      aria-label={label}
      value={value}
      orientation="horizontal"
      loop
      onValueChange={(next) => {
        const option = options.find((candidate) => candidate.value === next);
        if (option) onValueChange(option.value);
      }}
      className={clsx(
        "relative items-center gap-0.5 rounded-button border border-border bg-surface-2 p-0.5 shadow-[inset_0_1px_2px_rgba(0,0,0,0.08)]",
        fullWidth ? "flex w-full" : "inline-flex",
        className,
      )}
    >
      {options.map((option) => {
        const checked = option.value === value;
        const item = (
          <RadioGroup.Item
            key={option.value}
            value={option.value}
            disabled={option.disabled}
            aria-label={iconOnly ? option.label : undefined}
            className={clsx(
              "relative z-0 inline-flex flex-1 items-center justify-center gap-1.5 rounded-[9px] font-medium whitespace-nowrap transition-colors duration-150",
              "text-muted hover:text-text data-[state=checked]:text-text disabled:pointer-events-none disabled:opacity-45 [&_svg]:shrink-0",
              size === "md" ? "h-8 px-3 text-[13px] [&_svg]:size-4 pointer-coarse:h-10" : "h-7 px-2.5 text-[12px] [&_svg]:size-3.5 pointer-coarse:h-9",
            )}
          >
            {checked ? (
              <motion.span
                layoutId={thumbId}
                transition={SPRING}
                aria-hidden="true"
                className="absolute inset-0 -z-10 rounded-[9px] border border-border bg-surface elevation-card dark:bg-surface-3"
              />
            ) : null}
            {option.icon ? (
              <span className={clsx("inline-flex", checked ? "text-accent" : "text-faint")}>{option.icon}</span>
            ) : null}
            {iconOnly ? null : <span>{option.label}</span>}
          </RadioGroup.Item>
        );
        return iconOnly ? (
          <Tooltip key={option.value} content={option.label}>
            {item}
          </Tooltip>
        ) : (
          item
        );
      })}
    </RadioGroup.Root>
  );
}
