import clsx from "clsx";
import { motion } from "motion/react";
import { Tabs as TabsPrimitive } from "radix-ui";
import { createContext, useContext, useId, useMemo, useState, type ComponentPropsWithRef, type ReactNode } from "react";
import { SPRING } from "../../lib/motion";

type TabsVariant = "pill" | "underline";

interface TabsContextValue {
  value: string | undefined;
  indicatorId: string;
  variant: TabsVariant;
}

const TabsContext = createContext<TabsContextValue>({ value: undefined, indicatorId: "tabs", variant: "pill" });

export interface TabsProps
  extends Omit<ComponentPropsWithRef<typeof TabsPrimitive.Root>, "value" | "defaultValue" | "onValueChange"> {
  value?: string;
  defaultValue?: string;
  onValueChange?: (value: string) => void;
  /** "pill" = segmented control look (default); "underline" = page-level tabs. */
  variant?: TabsVariant;
}

/**
 * Tabs (Radix): arrow keys move between tabs, the active tab has an animated indicator.
 * Compose with `TabsList`, `TabsTrigger` and `TabsContent`.
 */
export function Tabs({ value: valueProp, defaultValue, onValueChange, variant = "pill", className, ...rest }: TabsProps) {
  const [uncontrolled, setUncontrolled] = useState(defaultValue);
  const value = valueProp ?? uncontrolled;
  const indicatorId = `tabs-${useId()}`;
  const context = useMemo(() => ({ value, indicatorId, variant }), [value, indicatorId, variant]);
  return (
    <TabsContext.Provider value={context}>
      <TabsPrimitive.Root
        {...rest}
        value={value}
        onValueChange={(next) => {
          if (valueProp === undefined) setUncontrolled(next);
          onValueChange?.(next);
        }}
        className={clsx("flex flex-col gap-4", className)}
      />
    </TabsContext.Provider>
  );
}

export function TabsList({ className, ...rest }: ComponentPropsWithRef<typeof TabsPrimitive.List>) {
  const { variant } = useContext(TabsContext);
  return (
    <TabsPrimitive.List
      {...rest}
      className={clsx(
        "relative flex max-w-full items-center overflow-x-auto",
        variant === "pill"
          ? "w-fit gap-0.5 rounded-button border border-border bg-surface-2 p-1 shadow-[inset_0_1px_2px_rgba(0,0,0,0.08)]"
          : "gap-1 border-b border-border",
        className,
      )}
    />
  );
}

export interface TabsTriggerProps extends ComponentPropsWithRef<typeof TabsPrimitive.Trigger> {
  icon?: ReactNode;
  /** Count shown after the label, e.g. the number of saved papers. */
  badge?: ReactNode;
}

export function TabsTrigger({ value, icon, badge, className, children, ...rest }: TabsTriggerProps) {
  const context = useContext(TabsContext);
  const active = context.value === value;
  const pill = context.variant === "pill";
  return (
    <TabsPrimitive.Trigger
      {...rest}
      value={value}
      className={clsx(
        "group relative z-0 inline-flex shrink-0 items-center justify-center gap-1.5 font-medium whitespace-nowrap transition-colors duration-150",
        "text-muted hover:text-text data-[state=active]:text-text disabled:pointer-events-none disabled:opacity-45 [&_svg]:size-4 [&_svg]:shrink-0",
        pill ? "h-8 rounded-[9px] px-3 text-[13px] pointer-coarse:h-10" : "h-10 px-3 text-sm",
        className,
      )}
    >
      {active ? (
        <motion.span
          layoutId={`${context.indicatorId}-indicator`}
          transition={SPRING}
          aria-hidden="true"
          className={clsx(
            "absolute -z-10",
            pill
              ? "inset-0 rounded-[9px] border border-border bg-surface elevation-card dark:bg-surface-3"
              : "inset-x-2 -bottom-px h-0.5 rounded-pill bg-accent shadow-[0_0_10px_var(--accent-glow)]",
          )}
        />
      ) : null}
      {icon ? <span className="inline-flex text-faint group-data-[state=active]:text-accent">{icon}</span> : null}
      {children}
      {badge !== undefined && badge !== null ? (
        <span className="rounded-pill bg-surface-3 px-1.5 py-0.5 text-[11px] leading-none text-muted tabular-nums group-data-[state=active]:bg-accent/15 group-data-[state=active]:text-text">
          {badge}
        </span>
      ) : null}
    </TabsPrimitive.Trigger>
  );
}

export function TabsContent({ className, ...rest }: ComponentPropsWithRef<typeof TabsPrimitive.Content>) {
  return <TabsPrimitive.Content {...rest} className={clsx("outline-none data-[state=active]:animate-fade-in", className)} />;
}
