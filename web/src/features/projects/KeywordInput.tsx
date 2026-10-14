/**
 * Keywords of a project (UI.md §10): a chip input next to the research question. Type a keyword or
 * phrase and press Enter or comma to add it; Backspace in the empty field removes the last one; a
 * pasted comma- or line-separated list adds every keyword in it. Duplicates (case, hyphen and plural
 * insensitive, like the server) and out-of-range lengths are refused inline.
 */
import clsx from "clsx";
import { Tag } from "lucide-react";
import { useId, useState, type ClipboardEvent, type KeyboardEvent } from "react";
import { Chip } from "../../components/ui/Chip";
import { Field } from "../../components/ui/Input";
import { MAX_KEYWORDS, cleanKeyword, keywordProblem, splitKeywords } from "./lib/keywords";

export interface KeywordInputProps {
  value: readonly string[];
  onChange: (next: string[]) => void;
  /** Server-side error for the list (e.g. from `details.fields`). */
  error?: string | null;
  disabled?: boolean;
}

export function KeywordInput({ value, onChange, error = null, disabled = false }: KeywordInputProps) {
  const autoId = useId();
  const id = `keywords-${autoId}`;
  const [draft, setDraft] = useState("");
  const [problem, setProblem] = useState<string | null>(null);
  const [announcement, setAnnouncement] = useState("");
  const shownError = problem ?? error;
  const full = value.length >= MAX_KEYWORDS;
  const hint = (
    <>
      Papers that mention these words in the title or abstract get a boost. Press Enter or comma to add one ({value.length}/{MAX_KEYWORDS}).
    </>
  );

  /** Adds `candidates` in order; returns the problem of the first one that was refused. */
  const add = (candidates: readonly string[]): string | null => {
    const next = [...value];
    let firstProblem: string | null = null;
    for (const candidate of candidates) {
      const reason = keywordProblem(candidate, next);
      if (reason) {
        firstProblem ??= reason;
        continue;
      }
      next.push(cleanKeyword(candidate));
    }
    if (next.length !== value.length) {
      onChange(next);
      const added = next.slice(value.length);
      setAnnouncement(`Added ${added.length === 1 ? `keyword ${added[0]}` : `${added.length} keywords`}.`);
    }
    setProblem(firstProblem);
    return firstProblem;
  };

  const commitDraft = () => {
    if (!cleanKeyword(draft)) {
      setDraft("");
      return;
    }
    if (add([draft]) === null) setDraft("");
  };

  const remove = (index: number) => {
    const keyword = value[index];
    onChange(value.filter((_, i) => i !== index));
    setProblem(null);
    setAnnouncement(`Removed keyword ${keyword}.`);
  };

  const onKeyDown = (event: KeyboardEvent<HTMLInputElement>) => {
    if (event.nativeEvent.isComposing) return;
    if (event.key === "Enter" || event.key === ",") {
      event.preventDefault();
      commitDraft();
    } else if (event.key === "Backspace" && draft === "" && value.length > 0) {
      event.preventDefault();
      remove(value.length - 1);
    }
  };

  const onPaste = (event: ClipboardEvent<HTMLInputElement>) => {
    const text = event.clipboardData.getData("text");
    if (!/[,;\n\r]/u.test(text)) return;
    event.preventDefault();
    const leftover = add(splitKeywords(`${draft}${text}`));
    if (leftover === null) setDraft("");
  };

  return (
    <Field label="Keywords" hint={hint} error={shownError} id={id}>
      <div
        className={clsx(
          "flex min-h-10 w-full flex-wrap items-center gap-1.5 rounded-control border bg-surface-2 px-2 py-1.5 shadow-[inset_0_1px_2px_rgba(0,0,0,0.06)]",
          "transition-[border-color,background-color] duration-150 focus-within:bg-surface",
          "has-[input:focus-visible]:outline-2 has-[input:focus-visible]:outline-offset-2 has-[input:focus-visible]:outline-accent",
          shownError ? "border-not-relevant" : "border-border hover:border-border-strong focus-within:border-accent",
          disabled && "opacity-50",
        )}
      >
        {value.length > 0 ? (
          <ul aria-label="Keywords added" className="contents">
            {value.map((keyword, index) => (
              <li key={keyword} className="max-w-full">
                <Chip
                  icon={<Tag aria-hidden="true" />}
                  onRemove={disabled ? undefined : () => remove(index)}
                  removeLabel={`Remove keyword “${keyword}”`}
                  className="bg-surface"
                >
                  {keyword}
                </Chip>
              </li>
            ))}
          </ul>
        ) : null}
        <input
          id={id}
          type="text"
          value={draft}
          disabled={disabled}
          maxLength={240}
          autoComplete="off"
          enterKeyHint="enter"
          aria-describedby={[`${id}-hint`, shownError ? `${id}-error` : null].filter(Boolean).join(" ")}
          aria-invalid={shownError ? true : undefined}
          placeholder={value.length === 0 ? "e.g. conformal prediction, retrieval-augmented generation" : full ? "" : "Add another"}
          onChange={(event) => {
            setDraft(event.target.value);
            if (problem) setProblem(null);
          }}
          onKeyDown={onKeyDown}
          onPaste={onPaste}
          onBlur={commitDraft}
          className="h-7 min-w-32 flex-1 bg-transparent px-1 text-sm text-text outline-none placeholder:text-faint disabled:cursor-not-allowed"
        />
      </div>
      <span role="status" aria-live="polite" className="sr-only">
        {announcement}
      </span>
    </Field>
  );
}
