/**
 * Accessibility basics of the design-system primitives (UI.md §11): real buttons with accessible
 * names, keyboard operation, focus return after dialogs, ARIA values, reduced-motion rendering.
 */
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { setMotionPreference } from "../../../lib/motion";
import { Button } from "../Button";
import { ConfirmDialog } from "../ConfirmDialog";
import { Dialog } from "../Dialog";
import { IconButton } from "../IconButton";
import { ProgressBar } from "../ProgressBar";
import { RadarSweep } from "../RadarSweep";
import { Switch } from "../Switch";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "../Tabs";
import { TooltipProvider } from "../Tooltip";

afterEach(() => {
  setMotionPreference("system");
});

describe("Button", () => {
  it("is a native button with its text as accessible name", async () => {
    const onClick = vi.fn();
    render(<Button onClick={onClick}>Rebuild deck</Button>);
    const button = screen.getByRole("button", { name: "Rebuild deck" });
    expect(button.tagName).toBe("BUTTON");
    await userEvent.click(button);
    expect(onClick).toHaveBeenCalledTimes(1);
  });

  it("blocks clicks while loading but stays focusable and announces busy", async () => {
    const onClick = vi.fn();
    render(
      <Button loading onClick={onClick}>
        Saving
      </Button>,
    );
    const button = screen.getByRole("button", { name: /Saving/ });
    expect(button).toHaveAttribute("aria-busy", "true");
    expect(button).toHaveAttribute("aria-disabled", "true");
    await userEvent.click(button);
    expect(onClick).not.toHaveBeenCalled();
  });
});

describe("IconButton", () => {
  it("uses its label as the accessible name", () => {
    render(
      <TooltipProvider>
        <IconButton label="Undo last action" icon={<span>↺</span>} />
      </TooltipProvider>,
    );
    expect(screen.getByRole("button", { name: "Undo last action" })).toBeInTheDocument();
  });
});

describe("Switch", () => {
  it("toggles with click and with the Space key", async () => {
    const onCheckedChange = vi.fn();
    render(<Switch label="Auto-expansion" onCheckedChange={onCheckedChange} />);
    const control = screen.getByRole("switch", { name: "Auto-expansion" });
    expect(control).toHaveAttribute("aria-checked", "false");
    await userEvent.click(control);
    expect(onCheckedChange).toHaveBeenLastCalledWith(true);
    expect(control).toHaveAttribute("aria-checked", "true");
    control.focus();
    await userEvent.keyboard(" ");
    expect(onCheckedChange).toHaveBeenLastCalledWith(false);
  });
});

describe("Dialog", () => {
  it("is labelled by its title, closes on Escape and returns focus to the trigger", async () => {
    render(
      <Dialog title="Keyboard shortcuts" trigger={<button type="button">Open shortcuts</button>}>
        <p>Press ? for help</p>
      </Dialog>,
    );
    const trigger = screen.getByRole("button", { name: "Open shortcuts" });
    await userEvent.click(trigger);
    expect(await screen.findByRole("dialog", { name: "Keyboard shortcuts" })).toBeInTheDocument();
    await userEvent.keyboard("{Escape}");
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(trigger).toHaveFocus();
  });
});

describe("ConfirmDialog", () => {
  it("calls onConfirm from the confirm button and closes via cancel", async () => {
    const onConfirm = vi.fn();
    const onOpenChange = vi.fn();
    render(
      <ConfirmDialog
        open
        onOpenChange={onOpenChange}
        title="Replace current deck?"
        confirmLabel="Replace"
        cancelLabel="Keep"
        onConfirm={onConfirm}
      />,
    );
    expect(screen.getByRole("alertdialog", { name: "Replace current deck?" })).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Replace" }));
    expect(onConfirm).toHaveBeenCalledTimes(1);
    await userEvent.click(screen.getByRole("button", { name: "Keep" }));
    expect(onOpenChange).toHaveBeenCalledWith(false);
  });
});

describe("Tabs", () => {
  it("moves between tabs with the arrow keys", async () => {
    render(
      <Tabs defaultValue="saved">
        <TabsList aria-label="Library">
          <TabsTrigger value="saved">Saved</TabsTrigger>
          <TabsTrigger value="pinned">Pinned</TabsTrigger>
        </TabsList>
        <TabsContent value="saved">Saved papers</TabsContent>
        <TabsContent value="pinned">Pinned papers</TabsContent>
      </Tabs>,
    );
    const saved = screen.getByRole("tab", { name: "Saved" });
    saved.focus();
    await userEvent.keyboard("{ArrowRight}");
    const pinned = screen.getByRole("tab", { name: "Pinned" });
    expect(pinned).toHaveFocus();
    expect(pinned).toHaveAttribute("aria-selected", "true");
    expect(screen.getByRole("tabpanel")).toHaveTextContent("Pinned papers");
  });
});

describe("ProgressBar", () => {
  it("exposes a labelled determinate progressbar in percent", () => {
    render(<ProgressBar value={0.42} label="Embedding papers" />);
    const bar = screen.getByRole("progressbar", { name: "Embedding papers" });
    expect(bar).toHaveAttribute("aria-valuenow", "42");
    expect(bar).toHaveAttribute("aria-valuemin", "0");
    expect(bar).toHaveAttribute("aria-valuemax", "100");
  });

  it("is indeterminate without a value", () => {
    render(<ProgressBar value={null} label="Waiting" />);
    expect(screen.getByRole("progressbar", { name: "Waiting" })).not.toHaveAttribute("aria-valuenow");
  });
});

describe("RadarSweep", () => {
  it("announces the job message and progress", () => {
    render(<RadarSweep message="Embedding 412 papers locally" progress={0.5} />);
    expect(screen.getByRole("status")).toHaveTextContent("Embedding 412 papers locally");
    expect(screen.getByRole("progressbar")).toHaveAttribute("aria-valuenow", "50");
  });

  it("renders a static scope under the reduced motion preference", () => {
    setMotionPreference("reduced");
    const { container } = render(<RadarSweep message="Working" />);
    expect(container.querySelector("[data-reduced]")).toHaveAttribute("data-reduced", "true");
  });
});
