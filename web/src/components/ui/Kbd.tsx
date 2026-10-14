import clsx from "clsx";
import type { ComponentPropsWithRef } from "react";

interface KeyInfo {
  glyph: string;
  label: string;
}

const KEYS: Readonly<Record<string, KeyInfo>> = {
  ArrowLeft: { glyph: "←", label: "Left arrow" },
  ArrowRight: { glyph: "→", label: "Right arrow" },
  ArrowUp: { glyph: "↑", label: "Up arrow" },
  ArrowDown: { glyph: "↓", label: "Down arrow" },
  Backspace: { glyph: "⌫", label: "Backspace" },
  Enter: { glyph: "↵", label: "Enter" },
  Escape: { glyph: "Esc", label: "Escape" },
  " ": { glyph: "Space", label: "Space" },
  Space: { glyph: "Space", label: "Space" },
  Tab: { glyph: "Tab", label: "Tab" },
  Shift: { glyph: "⇧", label: "Shift" },
  Meta: { glyph: "⌘", label: "Command" },
  Alt: { glyph: "⌥", label: "Option" },
  Control: { glyph: "Ctrl", label: "Control" },
};

/** Display glyph and spoken label for a `KeyboardEvent.key` value ("ArrowLeft" → "←", "Left arrow"). */
export function keyInfo(key: string): KeyInfo {
  const known = KEYS[key];
  if (known) return known;
  const glyph = key.length === 1 ? key.toUpperCase() : key;
  return { glyph, label: glyph };
}

export interface KbdProps extends ComponentPropsWithRef<"kbd"> {
  /** A `KeyboardEvent.key` value rendered as its glyph with a spoken label (e.g. "ArrowLeft"). */
  keyName?: string;
}

/** Keycap for shortcuts: `<Kbd>Z</Kbd>` or `<Kbd keyName="ArrowLeft" />`. */
export function Kbd({ keyName, className, children, ...rest }: KbdProps) {
  const info = keyName ? keyInfo(keyName) : null;
  return (
    <kbd
      aria-label={info && info.label !== info.glyph ? info.label : undefined}
      className={clsx(
        "inline-flex h-5 min-w-5 shrink-0 items-center justify-center rounded-[6px] border border-border-strong bg-surface-2 px-1.5",
        "font-sans text-[11px] leading-none font-semibold text-muted shadow-[inset_0_-1px_0_var(--border-strong)]",
        className,
      )}
      {...rest}
    >
      {info ? info.glyph : children}
    </kbd>
  );
}
