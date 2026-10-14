/** Deck header (UI.md §7): project, date, progress with a segmented bar, rebuild and more papers. */
import { Plus, RefreshCw } from "lucide-react";
import type { RecommendationItem } from "../../api/types";
import { Button } from "../../components/ui/Button";
import { IconButton } from "../../components/ui/IconButton";
import { STATE_META } from "../../lib/states";

export function DeckProgress({ items }: { items: readonly RecommendationItem[] }) {
  const decided = items.filter((item) => item.state !== null).length;
  return (
    <div className="flex w-full flex-col gap-2">
      <div className="flex items-baseline justify-between gap-3 text-[13px]">
        <span className="font-medium text-text tabular-nums">
          {decided} of {items.length}
        </span>
        <span className="text-muted">{items.length - decided === 0 ? "All done" : `${items.length - decided} to go`}</span>
      </div>
      <div className="flex h-1.5 w-full gap-[3px]" role="img" aria-label={`${decided} of ${items.length} papers decided`}>
        {items.map((item) => (
          <span
            key={item.paper.id}
            className="h-full min-w-0 flex-1 rounded-full transition-[background-color,box-shadow] duration-300"
            style={{
              backgroundColor: item.state ? STATE_META[item.state].color : "var(--surface-3)",
              boxShadow: item.state ? `0 0 8px -2px ${STATE_META[item.state].color}` : undefined,
            }}
          />
        ))}
      </div>
    </div>
  );
}

export interface DeckHeaderProps {
  projectName: string | undefined;
  generatedAt: string | null;
  items: readonly RecommendationItem[];
  busy: boolean;
  onRebuild: () => void;
  onMore: () => void;
}

export function DeckHeader({ projectName, generatedAt, items, busy, onRebuild, onMore }: DeckHeaderProps) {
  const date = new Intl.DateTimeFormat("en", { weekday: "long", month: "long", day: "numeric" }).format(
    generatedAt ? new Date(generatedAt) : new Date(),
  );
  return (
    <header className="mx-auto mb-5 flex w-full max-w-[560px] flex-col gap-4">
      <div className="flex items-end justify-between gap-4">
        <div className="min-w-0">
          <p className="truncate text-eyebrow">{projectName ?? " "}</p>
          <h1 id="page-title" className="truncate font-serif text-[22px] leading-tight font-semibold tracking-[-0.015em] text-text sm:text-[26px]">
            {date}
          </h1>
        </div>
        <div className="flex shrink-0 items-center gap-1.5">
          <Button size="sm" variant="ghost" leadingIcon={<Plus aria-hidden="true" />} onClick={onMore} disabled={busy} className="max-sm:hidden">
            More papers
          </Button>
          <IconButton label="More papers" icon={<Plus />} variant="ghost" size="sm" onClick={onMore} disabled={busy} className="sm:hidden" />
          <IconButton label="Rebuild today's deck" icon={<RefreshCw />} variant="ghost" size="sm" onClick={onRebuild} disabled={busy} />
        </div>
      </div>
      {items.length > 0 ? <DeckProgress items={items} /> : null}
    </header>
  );
}
