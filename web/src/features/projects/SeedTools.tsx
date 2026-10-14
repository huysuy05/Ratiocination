/**
 * Seed papers (UI.md §10): paste identifiers (one per line; posted one at a time, each animating in
 * as a mini card or an inline error) or search papers already known to this server; plus the list
 * of current seeds with Remove.
 */
import { AnimatePresence, motion } from "motion/react";
import { CircleAlert, LoaderCircle, Plus, Search, Sprout, X } from "lucide-react";
import { useState } from "react";
import { errorMessage } from "../../api/client";
import { useAddSeed, useRemoveSeed, useSearch, useSeeds } from "../../api/hooks";
import type { PaperSummary, Seed } from "../../api/types";
import { Button } from "../../components/ui/Button";
import { IconButton } from "../../components/ui/IconButton";
import { Input, Textarea } from "../../components/ui/Input";
import { Skeleton } from "../../components/ui/Skeleton";
import { formatAuthors } from "../../lib/format";
import { SPRING } from "../../lib/motion";

interface PendingSeed {
  key: string;
  identifier: string;
  status: "queued" | "adding" | "error";
  message?: string;
}

function PaperLine({ paper }: { paper: Pick<PaperSummary, "title" | "authors" | "year" | "venue"> }) {
  return (
    <span className="min-w-0 flex-1">
      <span className="line-clamp-2 font-serif text-[15px] leading-snug text-text">{paper.title}</span>
      <span className="mt-0.5 block truncate text-[12.5px] text-muted">
        {[formatAuthors(paper.authors, 2), paper.year, paper.venue].filter(Boolean).join(" · ")}
      </span>
    </span>
  );
}

export function SeedList({ projectId }: { projectId: number }) {
  const seeds = useSeeds(projectId);
  const remove = useRemoveSeed(projectId);
  if (seeds.isPending) return <Skeleton className="h-16" />;
  const items: Seed[] = seeds.data ?? [];
  if (items.length === 0) {
    return <p className="text-[13px] text-muted">No seed papers yet. Seeds anchor the deck to work you already trust.</p>;
  }
  return (
    <ul className="flex flex-col gap-2">
      <AnimatePresence initial={false}>
        {items.map((seed) => (
          <motion.li
            key={seed.paper.id}
            layout
            initial={{ opacity: 0, y: 6 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, x: -12 }}
            transition={SPRING}
            className="flex items-center gap-3 rounded-panel border border-border bg-surface px-3.5 py-2.5"
          >
            <Sprout aria-hidden="true" className="size-4 shrink-0 text-relevant" />
            <PaperLine paper={seed.paper} />
            <IconButton
              label={`Remove seed ${seed.paper.title}`}
              icon={<X />}
              variant="ghost"
              size="sm"
              disabled={remove.isPending && remove.variables === seed.paper.id}
              onClick={() => remove.mutate(seed.paper.id)}
            />
          </motion.li>
        ))}
      </AnimatePresence>
    </ul>
  );
}

export function SeedAdder({ projectId }: { projectId: number }) {
  const add = useAddSeed(projectId);
  const [text, setText] = useState("");
  const [pending, setPending] = useState<PendingSeed[]>([]);
  const [query, setQuery] = useState("");
  const search = useSearch(query, { limit: 8 });
  const seeds = useSeeds(projectId);
  const seedIds = new Set((seeds.data ?? []).map((seed) => seed.paper.id));

  const update = (key: string, patch: Partial<PendingSeed> | null) =>
    setPending((list) => (patch === null ? list.filter((p) => p.key !== key) : list.map((p) => (p.key === key ? { ...p, ...patch } : p))));

  const submitIdentifiers = async () => {
    const identifiers = [...new Set(text.split(/[\n,]+/).map((line) => line.trim()).filter(Boolean))];
    if (identifiers.length === 0) return;
    setText("");
    const queue = identifiers.map((identifier, index) => ({ key: `${Date.now()}-${index}`, identifier, status: "queued" as const }));
    setPending((list) => [...list, ...queue]);
    // One at a time: each lookup may reach arXiv/OpenAlex.
    for (const item of queue) {
      update(item.key, { status: "adding" });
      try {
        await add.mutateAsync({ identifier: item.identifier });
        update(item.key, null);
      } catch (error) {
        update(item.key, { status: "error", message: errorMessage(error, "Could not add this paper") });
      }
    }
  };

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-col gap-2">
        <label htmlFor={`seed-ids-${projectId}`} className="text-[13px] font-medium text-text">
          Paste identifiers
        </label>
        <Textarea
          id={`seed-ids-${projectId}`}
          rows={3}
          placeholder={"2310.06825\n10.1145/3397271.3401075\nW3016913119"}
          value={text}
          onChange={(event) => setText(event.target.value)}
          className="font-mono text-[13px]"
        />
        <div className="flex items-center justify-between gap-3">
          <p className="text-[12.5px] text-muted">arXiv ids, DOIs or OpenAlex ids — one per line.</p>
          <Button size="sm" variant="primary" leadingIcon={<Plus aria-hidden="true" />} onClick={() => void submitIdentifiers()} disabled={!text.trim()}>
            Add papers
          </Button>
        </div>
        <ul className="flex flex-col gap-1.5" aria-live="polite">
          <AnimatePresence initial={false}>
            {pending.map((item) => (
              <motion.li
                key={item.key}
                layout
                initial={{ opacity: 0, y: 4 }}
                animate={{ opacity: 1, y: 0 }}
                exit={{ opacity: 0, scale: 0.98 }}
                className="flex items-center gap-2.5 rounded-control border border-border bg-surface-2 px-3 py-2 text-[13px]"
              >
                {item.status === "error" ? (
                  <CircleAlert aria-hidden="true" className="size-4 shrink-0 text-not-relevant" />
                ) : (
                  <LoaderCircle aria-hidden="true" className="size-4 shrink-0 animate-spin text-accent" />
                )}
                <span className="font-mono">{item.identifier}</span>
                <span className={item.status === "error" ? "min-w-0 flex-1 truncate text-not-relevant" : "text-muted"}>
                  {item.status === "queued" ? "Queued" : item.status === "adding" ? "Looking it up…" : item.message}
                </span>
                {item.status === "error" ? (
                  <IconButton label="Dismiss" icon={<X />} variant="ghost" size="sm" onClick={() => update(item.key, null)} />
                ) : null}
              </motion.li>
            ))}
          </AnimatePresence>
        </ul>
      </div>
      <div className="flex flex-col gap-2">
        <Input
          aria-label="Search papers already on this server"
          leadingIcon={<Search />}
          placeholder="Or search papers already on this server"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
        />
        {query.trim() ? (
          <ul className="flex flex-col gap-1.5">
            {search.isPending ? <Skeleton className="h-12" /> : null}
            {search.data?.items.length === 0 ? <li className="px-1 text-[13px] text-muted">No match on this server.</li> : null}
            {search.data?.items.map((paper) => (
              <li key={paper.id} className="flex items-center gap-3 rounded-control border border-border bg-surface px-3 py-2">
                <PaperLine paper={paper} />
                <Button
                  size="sm"
                  variant={seedIds.has(paper.id) ? "ghost" : "secondary"}
                  disabled={seedIds.has(paper.id)}
                  loading={add.isPending && add.variables?.paper_id === paper.id}
                  onClick={() => add.mutate({ paper_id: paper.id })}
                >
                  {seedIds.has(paper.id) ? "Added" : "Add"}
                </Button>
              </li>
            ))}
          </ul>
        ) : null}
      </div>
    </div>
  );
}
