import clsx from "clsx";
import { Popover as PopoverPrimitive } from "radix-ui";
import type { ComponentPropsWithRef } from "react";
import { FLOATING_SURFACE_CLASS } from "./Menu";

/** Popover (Radix): `Popover` + `PopoverTrigger asChild` + `PopoverContent`. */
export const Popover = PopoverPrimitive.Root;
export const PopoverTrigger = PopoverPrimitive.Trigger;
export const PopoverAnchor = PopoverPrimitive.Anchor;
export const PopoverClose = PopoverPrimitive.Close;

export function PopoverContent({
  className,
  sideOffset = 8,
  collisionPadding = 8,
  ...rest
}: ComponentPropsWithRef<typeof PopoverPrimitive.Content>) {
  return (
    <PopoverPrimitive.Portal>
      <PopoverPrimitive.Content
        {...rest}
        sideOffset={sideOffset}
        collisionPadding={collisionPadding}
        className={clsx(
          FLOATING_SURFACE_CLASS,
          "w-[min(22rem,calc(100vw-1rem))] origin-(--radix-popover-content-transform-origin) p-4",
          className,
        )}
      />
    </PopoverPrimitive.Portal>
  );
}
