import clsx from "clsx";
import { Check } from "lucide-react";
import { DropdownMenu } from "radix-ui";
import type { ComponentPropsWithRef, ReactNode } from "react";
import { Kbd } from "./Kbd";

/** Dropdown menu (Radix): `Menu` + `MenuTrigger asChild` + `MenuContent` with `MenuItem`s. */
export const Menu = DropdownMenu.Root;
export const MenuTrigger = DropdownMenu.Trigger;
export const MenuGroup = DropdownMenu.Group;
export const MenuRadioGroup = DropdownMenu.RadioGroup;

export const FLOATING_SURFACE_CLASS =
  "z-[60] rounded-[14px] border border-border-strong bg-surface p-1.5 text-text elevation-popover outline-none " +
  "data-[state=closed]:animate-float-out data-[state=open]:animate-float-in";

export interface MenuContentProps extends ComponentPropsWithRef<typeof DropdownMenu.Content> {
  /** Render without a portal (rarely needed). */
  inline?: boolean;
}

export function MenuContent({ className, sideOffset = 6, align = "start", collisionPadding = 8, inline = false, ...rest }: MenuContentProps) {
  const content = (
    <DropdownMenu.Content
      {...rest}
      sideOffset={sideOffset}
      align={align}
      collisionPadding={collisionPadding}
      className={clsx(
        FLOATING_SURFACE_CLASS,
        "max-h-(--radix-dropdown-menu-content-available-height) min-w-[12rem] origin-(--radix-dropdown-menu-content-transform-origin) overflow-y-auto",
        className,
      )}
    />
  );
  return inline ? content : <DropdownMenu.Portal>{content}</DropdownMenu.Portal>;
}

const ITEM_CLASS =
  "relative flex h-9 cursor-pointer items-center gap-2.5 rounded-control px-2.5 text-[13.5px] outline-none select-none pointer-coarse:h-11 " +
  "transition-colors duration-100 data-[highlighted]:bg-surface-2 data-[disabled]:pointer-events-none data-[disabled]:opacity-45 " +
  "[&_svg]:size-4 [&_svg]:shrink-0";

export interface MenuItemProps extends ComponentPropsWithRef<typeof DropdownMenu.Item> {
  icon?: ReactNode;
  /** Shortcut hint, e.g. "R". */
  shortcut?: string;
  tone?: "default" | "danger";
}

export function MenuItem({ icon, shortcut, tone = "default", className, children, ...rest }: MenuItemProps) {
  return (
    <DropdownMenu.Item
      {...rest}
      className={clsx(ITEM_CLASS, tone === "danger" ? "text-not-relevant" : "text-text", className)}
    >
      {icon ? <span className={clsx("inline-flex", tone === "danger" ? "text-not-relevant" : "text-muted")}>{icon}</span> : null}
      <span className="min-w-0 flex-1 truncate">{children}</span>
      {shortcut ? <Kbd className="ml-auto">{shortcut}</Kbd> : null}
    </DropdownMenu.Item>
  );
}

export interface MenuRadioItemProps extends ComponentPropsWithRef<typeof DropdownMenu.RadioItem> {
  icon?: ReactNode;
}

export function MenuRadioItem({ icon, className, children, ...rest }: MenuRadioItemProps) {
  return (
    <DropdownMenu.RadioItem {...rest} className={clsx(ITEM_CLASS, "pr-8 text-text", className)}>
      {icon ? <span className="inline-flex text-muted">{icon}</span> : null}
      <span className="min-w-0 flex-1 truncate">{children}</span>
      <DropdownMenu.ItemIndicator className="absolute right-2.5 inline-flex text-accent">
        <Check aria-hidden="true" />
      </DropdownMenu.ItemIndicator>
    </DropdownMenu.RadioItem>
  );
}

export function MenuLabel({ className, ...rest }: ComponentPropsWithRef<typeof DropdownMenu.Label>) {
  return <DropdownMenu.Label {...rest} className={clsx("px-2.5 pt-1.5 pb-1 text-eyebrow", className)} />;
}

export function MenuSeparator({ className, ...rest }: ComponentPropsWithRef<typeof DropdownMenu.Separator>) {
  return <DropdownMenu.Separator {...rest} className={clsx("-mx-1.5 my-1.5 h-px bg-border", className)} />;
}
