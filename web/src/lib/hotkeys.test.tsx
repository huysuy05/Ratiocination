import { fireEvent, render } from "@testing-library/react";
import { useState } from "react";
import { describe, expect, it, vi } from "vitest";
import {
  formatHotkey,
  getActiveHotkeyScope,
  normalizeKey,
  shouldIgnoreHotkey,
  useHotkeys,
  useHotkeySuspension,
  type HotkeyMap,
  type HotkeyOptions,
  type HotkeyScope,
} from "./hotkeys";

function Bindings({ scope, map, options }: { scope: HotkeyScope; map: HotkeyMap; options?: HotkeyOptions }) {
  useHotkeys(scope, map, options);
  return null;
}

function Suspend({ active }: { active: boolean }) {
  useHotkeySuspension(active);
  return null;
}

function keyDown(target: Document | Element | Window, init: KeyboardEventInit & { keyCode?: number }) {
  return fireEvent.keyDown(target, init);
}

describe("useHotkeys", () => {
  it("calls the bound handler and prevents the default action", () => {
    const left = vi.fn();
    render(<Bindings scope="deck" map={{ ArrowLeft: left }} />);
    const allowed = keyDown(document.body, { key: "ArrowLeft" });
    expect(left).toHaveBeenCalledTimes(1);
    expect(allowed).toBe(false); // fireEvent returns false when preventDefault was called
  });

  it("matches letters case-insensitively and accepts aliases", () => {
    const save = vi.fn();
    const open = vi.fn();
    const close = vi.fn();
    render(<Bindings scope="deck" map={{ s: save, Space: open, Esc: close }} />);
    keyDown(document.body, { key: "S", shiftKey: true });
    keyDown(document.body, { key: " " });
    keyDown(document.body, { key: "Escape" });
    expect(save).toHaveBeenCalledTimes(1);
    expect(open).toHaveBeenCalledTimes(1);
    expect(close).toHaveBeenCalledTimes(1);
  });

  it("supports shifted symbols such as ? for the help dialog", () => {
    const help = vi.fn();
    render(<Bindings scope="deck" map={{ "?": help }} />);
    keyDown(document.body, { key: "?", shiftKey: true });
    expect(help).toHaveBeenCalledTimes(1);
  });

  it("uses the latest map without re-registering", () => {
    const first = vi.fn();
    const second = vi.fn();
    const { rerender } = render(<Bindings scope="deck" map={{ p: first }} />);
    rerender(<Bindings scope="deck" map={{ p: second }} />);
    keyDown(document.body, { key: "p" });
    expect(first).not.toHaveBeenCalled();
    expect(second).toHaveBeenCalledTimes(1);
  });

  it("ignores unbound keys and leaves their default alone", () => {
    render(<Bindings scope="deck" map={{ p: vi.fn() }} />);
    expect(keyDown(document.body, { key: "q" })).toBe(true);
  });

  it("stops listening after unmount", () => {
    const pin = vi.fn();
    const { unmount } = render(<Bindings scope="deck" map={{ p: pin }} />);
    unmount();
    keyDown(document.body, { key: "p" });
    expect(pin).not.toHaveBeenCalled();
    expect(getActiveHotkeyScope()).toBeNull();
  });
});

describe("ignore rules (UI.md §6)", () => {
  function setup() {
    const handlers = {
      ArrowLeft: vi.fn(),
      ArrowRight: vi.fn(),
      ArrowUp: vi.fn(),
      ArrowDown: vi.fn(),
      " ": vi.fn(),
      Enter: vi.fn(),
      r: vi.fn(),
    };
    render(<Bindings scope="deck" map={handlers} />);
    return handlers;
  }

  it.each([
    ["metaKey", { metaKey: true }],
    ["ctrlKey", { ctrlKey: true }],
    ["altKey", { altKey: true }],
  ])("ignores events with %s", (_name, modifiers) => {
    const handlers = setup();
    keyDown(document.body, { key: "r", ...modifiers });
    expect(handlers.r).not.toHaveBeenCalled();
  });

  it("allows Shift", () => {
    const handlers = setup();
    keyDown(document.body, { key: "R", shiftKey: true });
    expect(handlers.r).toHaveBeenCalledTimes(1);
  });

  it("ignores IME composition (isComposing and keyCode 229)", () => {
    const handlers = setup();
    keyDown(document.body, { key: "r", isComposing: true });
    keyDown(document.body, { key: "r", keyCode: 229 });
    expect(handlers.r).not.toHaveBeenCalled();
  });

  it("ignores events that were already handled (defaultPrevented)", () => {
    const handlers = setup();
    const target = document.createElement("div");
    document.body.appendChild(target);
    target.addEventListener("keydown", (event) => event.preventDefault());
    keyDown(target, { key: "ArrowRight" });
    expect(handlers.ArrowRight).not.toHaveBeenCalled();
    target.remove();
  });

  it.each([
    ["input", '<input aria-label="search" />'],
    ["textarea", '<textarea aria-label="notes"></textarea>'],
    ["select", '<select aria-label="pick"><option>a</option></select>'],
    ["contenteditable", '<div contenteditable="true"><span id="inner">text</span></div>'],
    ["contenteditable (empty attribute)", '<div contenteditable=""><span id="inner">text</span></div>'],
  ])("ignores keys typed into %s", (_name, html) => {
    const handlers = setup();
    const host = document.createElement("div");
    host.innerHTML = html;
    document.body.appendChild(host);
    const target = host.querySelector("#inner") ?? host.firstElementChild;
    if (!target) throw new Error("no target");
    keyDown(target, { key: "r" });
    keyDown(target, { key: "ArrowLeft" });
    expect(handlers.r).not.toHaveBeenCalled();
    expect(handlers.ArrowLeft).not.toHaveBeenCalled();
    host.remove();
  });

  it("does not treat contenteditable=false as editable", () => {
    const handlers = setup();
    const host = document.createElement("div");
    host.setAttribute("contenteditable", "false");
    document.body.appendChild(host);
    keyDown(host, { key: "r" });
    expect(handlers.r).toHaveBeenCalledTimes(1);
    host.remove();
  });

  it.each([
    ["button", "<button>Save</button>"],
    ["link", '<a href="/papers/1">Paper</a>'],
    ["role=button", '<div role="button" tabindex="0">x</div>'],
    ["role=menuitem", '<div role="menuitem" tabindex="0">x</div>'],
    ["role=tab", '<div role="tab" tabindex="0">x</div>'],
    ["role=switch", '<div role="switch" tabindex="0">x</div>'],
  ])("leaves Space/Enter to a focused %s", (_name, html) => {
    const handlers = setup();
    const host = document.createElement("div");
    host.innerHTML = html;
    document.body.appendChild(host);
    const target = host.firstElementChild;
    if (!target) throw new Error("no target");
    keyDown(target, { key: " " });
    keyDown(target, { key: "Enter" });
    expect(handlers[" "]).not.toHaveBeenCalled();
    expect(handlers.Enter).not.toHaveBeenCalled();
    // Other keys still work on buttons (e.g. arrows while an action button has focus).
    keyDown(target, { key: "ArrowLeft" });
    expect(handlers.ArrowLeft).toHaveBeenCalledTimes(1);
    host.remove();
  });

  it("handles Space/Enter on a focused card (not a button)", () => {
    const handlers = setup();
    const card = document.createElement("div");
    card.tabIndex = -1;
    card.setAttribute("aria-label", "Card 1 of 3: Test paper A");
    document.body.appendChild(card);
    keyDown(card, { key: " " });
    keyDown(card, { key: "Enter" });
    expect(handlers[" "]).toHaveBeenCalledTimes(1);
    expect(handlers.Enter).toHaveBeenCalledTimes(1);
    card.remove();
  });

  it.each(["menu", "tablist", "slider", "radiogroup", "listbox"])("leaves arrow keys to role=%s widgets", (role) => {
    const handlers = setup();
    const widget = document.createElement("div");
    widget.setAttribute("role", role);
    const child = document.createElement("div");
    child.tabIndex = 0;
    widget.appendChild(child);
    document.body.appendChild(widget);
    for (const key of ["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown"]) keyDown(child, { key });
    expect(handlers.ArrowLeft).not.toHaveBeenCalled();
    expect(handlers.ArrowRight).not.toHaveBeenCalled();
    expect(handlers.ArrowUp).not.toHaveBeenCalled();
    expect(handlers.ArrowDown).not.toHaveBeenCalled();
    // Letters still reach the deck from inside those widgets.
    keyDown(child, { key: "r" });
    expect(handlers.r).toHaveBeenCalledTimes(1);
    widget.remove();
  });

  it("ignores auto-repeat unless allowRepeat is set", () => {
    const right = vi.fn();
    const { rerender } = render(<Bindings scope="deck" map={{ ArrowRight: right }} />);
    const allowed = keyDown(document.body, { key: "ArrowRight", repeat: true });
    expect(right).not.toHaveBeenCalled();
    expect(allowed).toBe(false); // still no page scroll
    rerender(<Bindings scope="deck" map={{ ArrowRight: right }} options={{ allowRepeat: true }} />);
    keyDown(document.body, { key: "ArrowRight", repeat: true });
    expect(right).toHaveBeenCalledTimes(1);
  });

  it("shouldIgnoreHotkey can be used directly", () => {
    const input = document.createElement("input");
    document.body.appendChild(input);
    const event = new KeyboardEvent("keydown", { key: "r", bubbles: true });
    Object.defineProperty(event, "target", { value: input });
    expect(shouldIgnoreHotkey(event)).toBe(true);
    input.remove();
    expect(shouldIgnoreHotkey(new KeyboardEvent("keydown", { key: "r" }))).toBe(false);
  });
});

describe("scopes", () => {
  it("an open sheet suspends the deck, which resumes when the sheet closes", () => {
    const deckLeft = vi.fn();
    const sheetLeft = vi.fn();
    function Screen() {
      const [sheetOpen, setSheetOpen] = useState(false);
      return (
        <>
          <Bindings scope="deck" map={{ ArrowLeft: deckLeft }} />
          <Bindings scope="sheet" map={{ ArrowLeft: sheetLeft }} options={{ enabled: sheetOpen }} />
          <button type="button" onClick={() => setSheetOpen((open) => !open)}>
            toggle
          </button>
        </>
      );
    }
    const { getByRole } = render(<Screen />);
    expect(getActiveHotkeyScope()).toBe("deck");
    keyDown(document.body, { key: "ArrowLeft" });
    expect(deckLeft).toHaveBeenCalledTimes(1);

    fireEvent.click(getByRole("button", { name: "toggle" }));
    expect(getActiveHotkeyScope()).toBe("sheet");
    keyDown(document.body, { key: "ArrowLeft" });
    expect(sheetLeft).toHaveBeenCalledTimes(1);
    expect(deckLeft).toHaveBeenCalledTimes(1);

    fireEvent.click(getByRole("button", { name: "toggle" }));
    expect(getActiveHotkeyScope()).toBe("deck");
    keyDown(document.body, { key: "ArrowLeft" });
    expect(deckLeft).toHaveBeenCalledTimes(2);
  });

  it("a sheet with an empty map still suspends the deck", () => {
    const deckRight = vi.fn();
    render(
      <>
        <Bindings scope="deck" map={{ ArrowRight: deckRight }} />
        <Bindings scope="sheet" map={{}} />
      </>,
    );
    keyDown(document.body, { key: "ArrowRight" });
    expect(deckRight).not.toHaveBeenCalled();
  });

  it("the most recently mounted binding wins within a scope", () => {
    const older = vi.fn();
    const newer = vi.fn();
    render(
      <>
        <Bindings scope="deck" map={{ z: older }} />
        <Bindings scope="deck" map={{ z: newer }} />
      </>,
    );
    keyDown(document.body, { key: "z" });
    expect(newer).toHaveBeenCalledTimes(1);
    expect(older).not.toHaveBeenCalled();
  });

  it("useHotkeySuspension suspends every scope while active", () => {
    const pin = vi.fn();
    const { rerender } = render(
      <>
        <Bindings scope="deck" map={{ p: pin }} />
        <Suspend active />
      </>,
    );
    expect(getActiveHotkeyScope()).toBeNull();
    keyDown(document.body, { key: "p" });
    expect(pin).not.toHaveBeenCalled();
    rerender(
      <>
        <Bindings scope="deck" map={{ p: pin }} />
        <Suspend active={false} />
      </>,
    );
    keyDown(document.body, { key: "p" });
    expect(pin).toHaveBeenCalledTimes(1);
  });
});

describe("key helpers", () => {
  it("normalizes and formats keys", () => {
    expect(normalizeKey("ArrowLeft")).toBe("arrowleft");
    expect(normalizeKey("Space")).toBe(" ");
    expect(normalizeKey("Esc")).toBe("escape");
    expect(formatHotkey("ArrowLeft")).toBe("←");
    expect(formatHotkey("ArrowUp")).toBe("↑");
    expect(formatHotkey("s")).toBe("S");
    expect(formatHotkey("Backspace")).toBe("⌫");
    expect(formatHotkey("?")).toBe("?");
  });
});
