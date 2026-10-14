import { Monitor, Moon, Sun } from "lucide-react";
import { useCallback } from "react";
import { IconButton } from "../../components/ui/IconButton";
import type { TooltipSide } from "../../components/ui/Tooltip";
import { SegmentedControl, type SegmentedOption } from "../../components/ui/SegmentedControl";
import { useMotionPreference } from "../../lib/motion";
import { useTheme, type ThemePreference } from "../../lib/theme";

export const THEME_LABELS: Readonly<Record<ThemePreference, string>> = { dark: "Dark", light: "Light", system: "System" };
const THEME_ORDER: readonly ThemePreference[] = ["dark", "light", "system"];

const THEME_OPTIONS: readonly SegmentedOption<ThemePreference>[] = [
  { value: "dark", label: "Dark", icon: <Moon aria-hidden="true" /> },
  { value: "light", label: "Light", icon: <Sun aria-hidden="true" /> },
  { value: "system", label: "System", icon: <Monitor aria-hidden="true" /> },
];

type ViewTransitionDocument = Document & { startViewTransition?: (update: () => void) => unknown };

/** Theme setter that cross-fades the change through the View Transitions API (skipped when reduced). */
export function useThemeSwitch() {
  const { theme, resolved, setTheme } = useTheme();
  const { reduced } = useMotionPreference();
  const switchTheme = useCallback(
    (next: ThemePreference) => {
      if (next === theme) return;
      const doc = document as ViewTransitionDocument;
      if (!reduced && typeof doc.startViewTransition === "function") {
        try {
          doc.startViewTransition(() => setTheme(next));
          return;
        } catch {
          // fall through to an instant switch
        }
      }
      setTheme(next);
    },
    [reduced, setTheme, theme],
  );
  return { theme, resolved, switchTheme };
}

/** Dark / Light / System segmented control (expanded sidebar, More sheet). */
export function ThemeSegmented({ iconOnly = false, className }: { iconOnly?: boolean; className?: string }) {
  const { theme, switchTheme } = useThemeSwitch();
  return (
    <SegmentedControl
      label="Theme"
      value={theme}
      onValueChange={switchTheme}
      options={THEME_OPTIONS}
      iconOnly={iconOnly}
      size="sm"
      fullWidth
      className={className}
    />
  );
}

/** One button that cycles Dark → Light → System (icon rail, mobile top bar). */
export function ThemeCycleButton({ tooltipSide = "bottom", className }: { tooltipSide?: TooltipSide; className?: string }) {
  const { theme, switchTheme } = useThemeSwitch();
  const next = THEME_ORDER[(THEME_ORDER.indexOf(theme) + 1) % THEME_ORDER.length] ?? "dark";
  const Icon = theme === "dark" ? Moon : theme === "light" ? Sun : Monitor;
  return (
    <IconButton
      label={`Theme: ${THEME_LABELS[theme]}. Switch to ${THEME_LABELS[next]}`}
      icon={<Icon aria-hidden="true" />}
      tooltipSide={tooltipSide}
      onClick={() => switchTheme(next)}
      className={className}
    />
  );
}
