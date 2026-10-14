/** Password input with a show/hide toggle and an optional live length meter (10+ characters). */
import clsx from "clsx";
import { Eye, EyeOff } from "lucide-react";
import { useState, type ComponentPropsWithRef } from "react";
import { Input } from "../../components/ui/Input";
import { MIN_PASSWORD } from "./lib";

export interface PasswordInputProps extends Omit<ComponentPropsWithRef<"input">, "type"> {
  invalid?: boolean;
  /** Show the length meter under the field. */
  meter?: boolean;
}

export function PasswordInput({ meter = false, value, className, ...rest }: PasswordInputProps) {
  const [visible, setVisible] = useState(false);
  const length = typeof value === "string" ? value.length : 0;
  const fraction = Math.min(1, length / MIN_PASSWORD);
  return (
    <div className="flex flex-col gap-1.5">
      <div className="relative">
        <Input {...rest} value={value} type={visible ? "text" : "password"} className={clsx("pr-11", className)} />
        <button
          type="button"
          onClick={() => setVisible((v) => !v)}
          aria-label={visible ? "Hide password" : "Show password"}
          aria-pressed={visible}
          className="absolute top-1/2 right-1 grid size-9 -translate-y-1/2 place-items-center rounded-control text-faint transition-colors hover:text-text"
        >
          {visible ? <EyeOff aria-hidden="true" className="size-4" /> : <Eye aria-hidden="true" className="size-4" />}
        </button>
      </div>
      {meter ? (
        <div className="flex items-center gap-2.5" aria-live="polite">
          <span className="h-1 flex-1 overflow-hidden rounded-full bg-surface-3">
            <span
              className={clsx(
                "block h-full rounded-full transition-[width,background-color] duration-200",
                fraction >= 1 ? "bg-relevant" : "bg-save",
              )}
              style={{ width: `${Math.max(fraction * 100, length > 0 ? 6 : 0)}%` }}
            />
          </span>
          <span className={clsx("text-[12px] tabular-nums", fraction >= 1 ? "text-relevant" : "text-muted")}>
            {fraction >= 1 ? "Long enough" : `${length}/${MIN_PASSWORD}`}
          </span>
        </div>
      ) : null}
    </div>
  );
}
