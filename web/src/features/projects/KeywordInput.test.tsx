import { fireEvent, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { describe, expect, it } from "vitest";
import { KeywordInput } from "./KeywordInput";
import { cleanKeyword, keywordKey, keywordProblem, splitKeywords } from "./lib/keywords";

function Harness({ initial = [], error = null }: { initial?: string[]; error?: string | null }) {
  const [value, setValue] = useState<string[]>(initial);
  return (
    <>
      <KeywordInput value={value} onChange={setValue} error={error} />
      <pre data-testid="value">{JSON.stringify(value)}</pre>
      <button type="button">Elsewhere</button>
    </>
  );
}

function setup(props: { initial?: string[]; error?: string | null } = {}) {
  const user = userEvent.setup();
  render(<Harness {...props} />);
  const input = screen.getByRole("textbox", { name: "Keywords" });
  const value = () => JSON.parse(screen.getByTestId("value").textContent ?? "[]") as string[];
  return { user, input, value };
}

describe("keyword rules", () => {
  it("normalises like the server", () => {
    expect(keywordKey("Self-Supervised  LLMs")).toBe("self supervised llm");
    expect(keywordKey("self supervised llm")).toBe("self supervised llm");
    expect(keywordKey("Ｃonformal Predictions")).toBe("conformal prediction");
    expect(keywordKey("C++")).toBe("c++");
    expect(keywordKey("analysis")).toBe("analysis");
    expect(keywordKey("--")).toBe("");
    expect(cleanKeyword("  conformal \n prediction ")).toBe("conformal prediction");
    expect(splitKeywords("RAG, conformal prediction;\nLLM,,")).toEqual(["RAG", "conformal prediction", "LLM"]);
  });

  it("explains why a keyword cannot be added", () => {
    expect(keywordProblem("x", [])).toBe("Keywords are 2–60 characters long.");
    expect(keywordProblem("y".repeat(61), [])).toBe("Keywords are 2–60 characters long.");
    expect(keywordProblem("--", [])).toBe("A keyword needs at least one letter or digit.");
    expect(keywordProblem("llms", ["LLM"])).toBe("“LLM” is already in the list.");
    expect(keywordProblem("new one", Array.from({ length: 12 }, (_, i) => `keyword ${i}`))).toBe("Up to 12 keywords.");
    expect(keywordProblem("RAG", ["LLM"])).toBeNull();
  });
});

describe("KeywordInput", () => {
  it("is a labelled field with a helper text", () => {
    const { input } = setup();
    expect(input).toHaveAccessibleDescription(/Papers that mention these words in the title or abstract get a boost/);
    expect(input).toHaveAccessibleDescription(/\(0\/12\)/);
  });

  it("adds keywords with Enter and comma, and removes the last one with Backspace", async () => {
    const { user, input, value } = setup();
    await user.type(input, "conformal   prediction{Enter}");
    expect(value()).toEqual(["conformal prediction"]);
    expect(input).toHaveValue("");
    await user.type(input, "RAG,");
    expect(value()).toEqual(["conformal prediction", "RAG"]);
    const list = screen.getByRole("list", { name: "Keywords added" });
    expect(within(list).getAllByRole("listitem").map((item) => item.textContent)).toEqual(["conformal prediction", "RAG"]);
    expect(screen.getByRole("status")).toHaveTextContent("Added keyword RAG.");
    await user.type(input, "{Backspace}");
    expect(value()).toEqual(["conformal prediction"]);
    expect(screen.getByRole("status")).toHaveTextContent("Removed keyword RAG.");
    await user.type(input, "ab{Backspace}{Backspace}{Backspace}");
    expect(value()).toEqual([]);
    expect(input).toHaveFocus();
  });

  it("removes a keyword with its button", async () => {
    const { user, value } = setup({ initial: ["LLM", "RAG"] });
    await user.click(screen.getByRole("button", { name: "Remove keyword “LLM”" }));
    expect(value()).toEqual(["RAG"]);
  });

  it("refuses duplicates and bad lengths inline, keeping the text to fix", async () => {
    const { user, input, value } = setup({ initial: ["LLM"] });
    await user.type(input, "LLMs{Enter}");
    expect(value()).toEqual(["LLM"]);
    expect(screen.getByText("“LLM” is already in the list.")).toBeInTheDocument();
    expect(input).toHaveAttribute("aria-invalid", "true");
    expect(input).toHaveValue("LLMs");
    await user.clear(input);
    expect(screen.queryByText("“LLM” is already in the list.")).not.toBeInTheDocument();
    await user.type(input, "x{Enter}");
    expect(screen.getByText("Keywords are 2–60 characters long.")).toBeInTheDocument();
    expect(value()).toEqual(["LLM"]);
  });

  it("adds every keyword of a pasted list and reports the ones it skipped", async () => {
    const { user, input, value } = setup({ initial: ["RAG"] });
    await user.click(input);
    await user.paste("conformal prediction, rag, self-supervised\nLLM");
    expect(value()).toEqual(["RAG", "conformal prediction", "self-supervised", "LLM"]);
    expect(screen.getByText("“RAG” is already in the list.")).toBeInTheDocument();
    expect(input).toHaveValue("");
    await user.paste("single words stay in the field");
    expect(input).toHaveValue("single words stay in the field");
  });

  it("stops at twelve keywords", async () => {
    const { user, input, value } = setup({ initial: Array.from({ length: 12 }, (_, i) => `keyword ${i}`) });
    await user.type(input, "one more{Enter}");
    expect(value()).toHaveLength(12);
    expect(screen.getByText("Up to 12 keywords.")).toBeInTheDocument();
  });

  it("adds the typed keyword when the field loses focus", async () => {
    const { user, input, value } = setup();
    await user.type(input, "retrieval");
    await user.click(screen.getByRole("button", { name: "Elsewhere" }));
    expect(value()).toEqual(["retrieval"]);
  });

  it("shows a server error", () => {
    setup({ error: "String should have at least 2 characters" });
    expect(screen.getByText("String should have at least 2 characters")).toBeInTheDocument();
  });

  it("ignores Enter while an IME composition is running", () => {
    const { input, value } = setup();
    fireEvent.change(input, { target: { value: "かな" } });
    fireEvent.keyDown(input, { key: "Enter", isComposing: true });
    expect(value()).toEqual([]);
  });
});
