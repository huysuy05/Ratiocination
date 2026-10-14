/** The deck's action buttons (UI.md §7): Not relevant, Undo, Save, Relevant, Read. */
import clsx from "clsx";
import { Bookmark, Check, Eye, RotateCcw, X, type LucideIcon } from "lucide-react";
import { motion } from "motion/react";
import { Kbd } from "../../components/ui/Kbd";
import { Tooltip } from "../../components/ui/Tooltip";
import type { Decision } from "./SwipeCard";

interface ActionButtonProps {
  label: string;
  icon: LucideIcon;
  shortcut: string;
  color: string;
  size?: "md" | "lg";
  disabled?: boolean;
  pressed?: boolean;
  onClick: () => void;
}

function ActionButton({ label, icon: Icon, shortcut, color, size = "md", disabled, pressed, onClick }: ActionButtonProps) {
  return (
    <div className="flex flex-col items-center gap-1.5">
      <Tooltip content={label} shortcut={shortcut}>
        <motion.button
          type="button"
          aria-label={label}
          aria-pressed={pressed}
          aria-keyshortcuts={shortcut}
          disabled={disabled}
          onClick={onClick}
          whileTap={disabled ? undefined : { scale: 0.92 }}
          style={{ ["--tone" as string]: color }}
          className={clsx(
            "grid place-items-center rounded-full border bg-surface transition-[box-shadow,background-color,border-color] duration-200",
            "border-(--tone)/35 text-(--tone) hover:bg-(--tone)/10 hover:shadow-[0_0_28px_-6px_var(--tone)] focus-visible:shadow-[0_0_0_3px_var(--tone)]",
            "disabled:pointer-events-none disabled:opacity-35",
            pressed && "bg-(--tone)/14",
            size === "lg" ? "size-16 [&_svg]:size-7" : "size-12 [&_svg]:size-5",
          )}
        >
          <Icon aria-hidden="true" strokeWidth={2.4} />
        </motion.button>
      </Tooltip>
      <span className="hidden text-[11.5px] font-medium text-muted sm:flex sm:items-center sm:gap-1">
        {label}
        <Kbd className="scale-90">{shortcut}</Kbd>
      </span>
    </div>
  );
}

export interface ActionBarProps {
  onDecide: (action: Decision) => void;
  onUndo: () => void;
  onToggleRead: () => void;
  canUndo: boolean;
  read: boolean;
  disabled: boolean;
}

export function ActionBar({ onDecide, onUndo, onToggleRead, canUndo, read, disabled }: ActionBarProps) {
  return (
    <div role="toolbar" aria-label="Deck actions" className="flex items-end justify-center gap-3 sm:gap-5">
      <ActionButton label="Undo" icon={RotateCcw} shortcut="Z" color="var(--text-muted)" disabled={!canUndo} onClick={onUndo} />
      <ActionButton label="Not relevant" icon={X} shortcut="←" color="var(--not-relevant)" size="lg" disabled={disabled} onClick={() => onDecide("not_relevant")} />
      <ActionButton label="Save" icon={Bookmark} shortcut="↑" color="var(--save)" disabled={disabled} onClick={() => onDecide("save")} />
      <ActionButton label="Relevant" icon={Check} shortcut="→" color="var(--relevant)" size="lg" disabled={disabled} onClick={() => onDecide("relevant")} />
      <ActionButton label={read ? "Mark unread" : "Mark read"} icon={Eye} shortcut="R" color="var(--read)" disabled={disabled} pressed={read} onClick={onToggleRead} />
    </div>
  );
}
