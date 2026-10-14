/**
 * Searchable multi-select over arXiv categories (UI.md §10): selected chips, a filter box, grouped
 * checkable rows, and free entry of any valid code that is not in the list.
 */
import clsx from "clsx";
import { Check, Plus, Search } from "lucide-react";
import { useId, useMemo, useState } from "react";
import { Chip } from "../../components/ui/Chip";
import { Input } from "../../components/ui/Input";
import { CATEGORY_GROUPS, CATEGORY_NAMES, CATEGORY_PATTERN, MAX_CATEGORIES, categoryLabel, matchesCategory } from "./categories";

export interface CategoryPickerProps {
  value: readonly string[];
  onChange: (next: string[]) => void;
  error?: string | null;
}

export function CategoryPicker({ value, onChange, error }: CategoryPickerProps) {
  const [query, setQuery] = useState("");
  const listId = useId();
  const selected = useMemo(() => new Set(value), [value]);
  const full = value.length >= MAX_CATEGORIES;
  const typed = query.trim();
  const canAddTyped = CATEGORY_PATTERN.test(typed) && !CATEGORY_NAMES.has(typed) && !selected.has(typed) && !full;

  const toggle = (code: string) => {
    if (selected.has(code)) onChange(value.filter((c) => c !== code));
    else if (!full) onChange([...value, code]);
  };

  const groups = CATEGORY_GROUPS.map((group) => ({
    ...group,
    categories: group.categories.filter((category) => matchesCategory(category, query)),
  })).filter((group) => group.categories.length > 0);

  return (
    <div className="flex flex-col gap-3">
      <div className="flex min-h-9 flex-wrap items-center gap-1.5" aria-live="polite">
        {value.length === 0 ? (
          <span className="text-[13px] text-muted">No categories yet — pick the arXiv listings your field publishes in.</span>
        ) : (
          value.map((code) => (
            <Chip key={code} tone="accent" onRemove={() => toggle(code)} removeLabel={`Remove ${categoryLabel(code)}`}>
              {categoryLabel(code)}
            </Chip>
          ))
        )}
      </div>
      <div className="flex items-center gap-3">
        <Input
          aria-label="Search arXiv categories"
          aria-controls={listId}
          leadingIcon={<Search />}
          placeholder="Search, e.g. retrieval or cs.IR"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter" && canAddTyped) {
              event.preventDefault();
              onChange([...value, typed]);
              setQuery("");
            }
          }}
        />
        <span className="shrink-0 text-[12.5px] text-muted tabular-nums">
          {value.length}/{MAX_CATEGORIES}
        </span>
      </div>
      {error ? (
        <p role="alert" className="text-[12.5px] font-medium text-not-relevant">
          {error}
        </p>
      ) : null}
      <div id={listId} className="max-h-80 overflow-y-auto rounded-panel border border-border bg-surface-2/50 p-1.5">
        {canAddTyped ? (
          <button
            type="button"
            onClick={() => {
              onChange([...value, typed]);
              setQuery("");
            }}
            className="flex w-full items-center gap-2.5 rounded-control px-3 py-2 text-left text-[13.5px] text-accent hover:bg-surface-3"
          >
            <Plus aria-hidden="true" className="size-4" />
            Add <span className="font-mono">{typed}</span>
          </button>
        ) : null}
        {groups.length === 0 && !canAddTyped ? (
          <p className="px-3 py-6 text-center text-[13px] text-muted">
            No category matches. Type a full code (like <span className="font-mono">cs.IR</span>) to add it.
          </p>
        ) : null}
        {groups.map((group) => (
          <section key={group.label} className="py-1" aria-label={group.label}>
            <h4 className="px-3 pt-1.5 pb-1 text-eyebrow">{group.label}</h4>
            <ul className="grid gap-0.5 sm:grid-cols-2">
              {group.categories.map((category) => {
                const checked = selected.has(category.code);
                return (
                  <li key={category.code}>
                    <button
                      type="button"
                      role="checkbox"
                      aria-checked={checked}
                      disabled={!checked && full}
                      onClick={() => toggle(category.code)}
                      className={clsx(
                        "flex w-full items-center gap-2.5 rounded-control px-3 py-2 text-left transition-colors pointer-coarse:min-h-11",
                        checked ? "bg-accent/10 text-text" : "text-muted hover:bg-surface-3 hover:text-text",
                        "disabled:cursor-not-allowed disabled:opacity-45",
                      )}
                    >
                      <span
                        aria-hidden="true"
                        className={clsx(
                          "grid size-4 shrink-0 place-items-center rounded-[5px] border transition-colors",
                          checked ? "border-accent bg-accent text-on-accent" : "border-border-strong",
                        )}
                      >
                        {checked ? <Check className="size-3" strokeWidth={3} /> : null}
                      </span>
                      <span className="min-w-0 flex-1 truncate text-[13.5px]">{category.name}</span>
                      <span className="shrink-0 font-mono text-[11.5px] text-faint">{category.code}</span>
                    </button>
                  </li>
                );
              })}
            </ul>
          </section>
        ))}
      </div>
    </div>
  );
}
