/**
 * Ratiocination design-system primitives (UI.md §12). FROZEN after the foundation phase.
 * Import from "@/components/ui" (or the individual files). Every component is themed through the
 * tokens in styles/tokens.css, keyboard accessible, and respects the motion preference.
 */
export { Badge, type BadgeProps, type BadgeSize, type BadgeVariant } from "./Badge";
export { BrandLockup, BrandMark, type BrandLockupProps, type BrandMarkProps } from "./BrandMark";
export { Button, buttonClassName, type ButtonProps, type ButtonSize, type ButtonStyleOptions, type ButtonVariant } from "./Button";
export { Card, CardDescription, CardFooter, CardHeader, CardTitle, type CardPadding, type CardProps, type CardTitleProps, type CardVariant } from "./Card";
export { Chip, ToggleChip, type ChipProps, type ChipSize, type ToggleChipProps } from "./Chip";
export { ConfirmDialog, type ConfirmDialogProps } from "./ConfirmDialog";
export { Dialog, DialogClose, DialogFooter, type DialogProps, type DialogSize } from "./Dialog";
export { EmptyState, type EmptyStateProps, type EmptyStateSize } from "./EmptyState";
export { IconButton, type IconButtonProps, type IconButtonSize, type IconButtonVariant } from "./IconButton";
export { Field, Input, Textarea, type FieldProps, type InputProps, type TextareaProps } from "./Input";
export { Kbd, keyInfo, type KbdProps } from "./Kbd";
export {
  Menu,
  MenuContent,
  MenuGroup,
  MenuItem,
  MenuLabel,
  MenuRadioGroup,
  MenuRadioItem,
  MenuSeparator,
  MenuTrigger,
  type MenuContentProps,
  type MenuItemProps,
  type MenuRadioItemProps,
} from "./Menu";
export { Popover, PopoverAnchor, PopoverClose, PopoverContent, PopoverTrigger } from "./Popover";
export { ProgressBar, type ProgressBarProps, type ProgressBarSize } from "./ProgressBar";
export {
  RadarBackdrop,
  RadarScope,
  RadarSweep,
  useReducedMotionPreference,
  type RadarBackdropProps,
  type RadarScopeProps,
  type RadarSweepProps,
  type RadarSweepSize,
} from "./RadarSweep";
export { SegmentedControl, type SegmentedControlProps, type SegmentedOption } from "./SegmentedControl";
export { Sheet, SheetClose, type SheetProps, type SheetSide, type SheetSize } from "./Sheet";
export { LoadingRegion, Skeleton, SkeletonText, type LoadingRegionProps, type SkeletonProps, type SkeletonTextProps } from "./Skeleton";
export { Slider, type SliderProps } from "./Slider";
export { Spinner, type SpinnerProps } from "./Spinner";
export { Switch, type SwitchProps } from "./Switch";
export { Tabs, TabsContent, TabsList, TabsTrigger, type TabsProps, type TabsTriggerProps } from "./Tabs";
export { TONE_COLOR, toneColor, type CSSVariables, type Tone } from "./tones";
export { Tooltip, TooltipProvider, type TooltipProps, type TooltipProviderProps, type TooltipSide } from "./Tooltip";
export { PageHeader, type PageHeaderProps } from "./PageHeader";
