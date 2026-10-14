import clsx from "clsx";
import { CircleAlert } from "lucide-react";
import { createContext, useContext, useId, type ComponentPropsWithRef, type ReactNode } from "react";

interface FieldContextValue {
  id: string;
  describedBy: string | undefined;
  invalid: boolean;
  required: boolean;
}

const FieldContext = createContext<FieldContextValue | null>(null);

export interface FieldProps {
  label: ReactNode;
  /** Help text (aria-describedby). */
  hint?: ReactNode;
  /** Error message (aria-describedby + aria-invalid). */
  error?: ReactNode;
  required?: boolean;
  /** Id of the control (default: generated). */
  id?: string;
  hideLabel?: boolean;
  className?: string;
  children: ReactNode;
}

/** Label + hint + error around one `Input`/`Textarea` (wired through context). */
export function Field({ label, hint, error, required = false, id, hideLabel = false, className, children }: FieldProps) {
  const autoId = useId();
  const controlId = id ?? `field-${autoId}`;
  const hintId = hint ? `${controlId}-hint` : undefined;
  const errorId = error ? `${controlId}-error` : undefined;
  const describedBy = [hintId, errorId].filter(Boolean).join(" ") || undefined;
  return (
    <FieldContext.Provider value={{ id: controlId, describedBy, invalid: Boolean(error), required }}>
      <div className={clsx("flex flex-col gap-1.5", className)}>
        <label htmlFor={controlId} className={clsx("text-[13px] font-medium text-text", hideLabel && "sr-only")}>
          {label}
          {required ? (
            <span aria-hidden="true" className="text-not-relevant">
              {" "}
              *
            </span>
          ) : null}
        </label>
        {children}
        {hint ? (
          <p id={hintId} className="text-[12.5px] text-muted">
            {hint}
          </p>
        ) : null}
        {error ? (
          <p id={errorId} className="flex items-start gap-1.5 text-[12.5px] font-medium text-not-relevant">
            <CircleAlert aria-hidden="true" className="mt-px size-3.5 shrink-0" />
            <span>{error}</span>
          </p>
        ) : null}
      </div>
    </FieldContext.Provider>
  );
}

const CONTROL_CLASS =
  "w-full min-w-0 rounded-control border border-border bg-surface-2 px-3 text-sm text-text shadow-[inset_0_1px_2px_rgba(0,0,0,0.06)] " +
  "transition-[border-color,background-color,box-shadow] duration-150 placeholder:text-faint hover:border-border-strong " +
  "focus:border-accent focus:bg-surface aria-[invalid=true]:border-not-relevant disabled:cursor-not-allowed disabled:opacity-50";

function useFieldProps(id: string | undefined, describedBy: string | undefined, invalid: boolean | undefined, required: boolean | undefined) {
  const field = useContext(FieldContext);
  return {
    id: id ?? field?.id,
    "aria-describedby": describedBy ?? field?.describedBy,
    "aria-invalid": (invalid ?? field?.invalid) ? true : undefined,
    required: required ?? (field?.required || undefined),
  };
}

export interface InputProps extends ComponentPropsWithRef<"input"> {
  invalid?: boolean;
  /** Icon inside the field on the left (e.g. a search glass). */
  leadingIcon?: ReactNode;
}

/** Text input; inside a `Field` it picks up the id, description and invalid state. */
export function Input({ id, invalid, required, leadingIcon, className, "aria-describedby": describedBy, ...rest }: InputProps) {
  const fieldProps = useFieldProps(id, describedBy, invalid, required);
  const input = (
    <input
      {...rest}
      {...fieldProps}
      className={clsx(CONTROL_CLASS, "h-10 pointer-coarse:h-11", leadingIcon ? "pl-9" : undefined, className)}
    />
  );
  if (!leadingIcon) return input;
  return (
    <div className="relative">
      <span aria-hidden="true" className="pointer-events-none absolute top-1/2 left-3 inline-flex -translate-y-1/2 text-faint [&_svg]:size-4">
        {leadingIcon}
      </span>
      {input}
    </div>
  );
}

export interface TextareaProps extends ComponentPropsWithRef<"textarea"> {
  invalid?: boolean;
}

export function Textarea({ id, invalid, required, className, "aria-describedby": describedBy, ...rest }: TextareaProps) {
  const fieldProps = useFieldProps(id, describedBy, invalid, required);
  return <textarea {...rest} {...fieldProps} className={clsx(CONTROL_CLASS, "min-h-24 resize-y py-2.5 leading-relaxed", className)} />;
}
