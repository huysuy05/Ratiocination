/** The project library (UI.md §11): everything marked relevant or saved, searchable, exportable. */
import { BookOpen, Download, Search } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { toast } from "sonner";
import { errorMessage } from "../api/client";
import { useExportBibtex, useLibrary, useProject, type LibraryStateFilter, type ReadFilter } from "../api/hooks";
import { usePaperSheetParam, useProjectIdParam } from "../app/params";
import { Button } from "../components/ui/Button";
import { EmptyState } from "../components/ui/EmptyState";
import { Input } from "../components/ui/Input";
import { PageHeader } from "../components/ui/PageHeader";
import { SegmentedControl } from "../components/ui/SegmentedControl";
import { Skeleton } from "../components/ui/Skeleton";
import { Tabs, TabsList, TabsTrigger } from "../components/ui/Tabs";
import { LibraryCard } from "../features/library/LibraryCard";
import { useDebouncedValue } from "../features/library/useDebouncedValue";
import { PaperDetailSheet } from "../features/paper/PaperDetailSheet";

type Tab = "all" | LibraryStateFilter;
const TAB_STATES: Readonly<Record<Tab, LibraryStateFilter[] | null>> = { all: null, relevant: ["relevant"], saved: ["saved"] };

export default function LibraryPage() {
  const projectId = useProjectIdParam();
  const project = useProject(projectId);
  const sheet = usePaperSheetParam();
  const [tab, setTab] = useState<Tab>("all");
  const [read, setRead] = useState<ReadFilter>("any");
  const [query, setQuery] = useState("");
  const debouncedQuery = useDebouncedValue(query);
  const library = useLibrary(projectId, { states: TAB_STATES[tab], read, query: debouncedQuery });
  const exportBib = useExportBibtex(projectId);
  const sentinel = useRef<HTMLDivElement>(null);
  const { hasNextPage, isFetchingNextPage, fetchNextPage } = library;

  // Load the next page when the end of the grid scrolls into view.
  useEffect(() => {
    const node = sentinel.current;
    if (!node || !hasNextPage) return;
    const observer = new IntersectionObserver((entries) => {
      if (entries.some((entry) => entry.isIntersecting) && !isFetchingNextPage) void fetchNextPage();
    });
    observer.observe(node);
    return () => observer.disconnect();
  }, [hasNextPage, isFetchingNextPage, fetchNextPage]);

  if (projectId === null) return null;
  const stats = project.data?.stats;
  const items = library.data?.items ?? [];
  const filtering = debouncedQuery.trim() !== "" || read !== "any";

  return (
    <section aria-labelledby="page-title" className="mx-auto w-full max-w-6xl">
      <PageHeader
        eyebrow={project.data?.name}
        title="Library"
        description="Every paper you marked relevant or saved in this project."
        actions={
          <Button
            leadingIcon={<Download aria-hidden="true" />}
            loading={exportBib.isPending}
            onClick={() =>
              exportBib.mutate(tab === "all" ? undefined : { states: TAB_STATES[tab] ?? undefined }, {
                onSuccess: (result) => toast.success(`Downloaded ${result.filename}`),
                onError: (error) => toast.error(errorMessage(error, "Could not export BibTeX")),
              })
            }
          >
            Export BibTeX
          </Button>
        }
      />
      <div className="mb-6 flex flex-col gap-3 lg:flex-row lg:items-center">
        <Tabs value={tab} onValueChange={(value) => setTab(value as Tab)}>
          <TabsList aria-label="Library state">
            <TabsTrigger value="all" badge={stats ? stats.relevant + stats.saved : undefined}>
              All
            </TabsTrigger>
            <TabsTrigger value="relevant" badge={stats?.relevant}>
              Relevant
            </TabsTrigger>
            <TabsTrigger value="saved" badge={stats?.saved}>
              Saved
            </TabsTrigger>
          </TabsList>
        </Tabs>
        <SegmentedControl
          label="Read status"
          size="sm"
          value={read}
          onValueChange={setRead}
          options={[
            { value: "any", label: "Any" },
            { value: "unread", label: "Unread" },
            { value: "read", label: "Read" },
          ]}
        />
        <div className="lg:ml-auto lg:w-80">
          <Input
            aria-label="Search the library"
            leadingIcon={<Search />}
            placeholder="Search titles and abstracts"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
          />
        </div>
      </div>
      {library.data && filtering ? (
        <p className="mb-3 text-[13px] text-muted" aria-live="polite">
          {library.data.total} result{library.data.total === 1 ? "" : "s"}
        </p>
      ) : null}
      {library.isPending ? (
        <ul className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
          {[0, 1, 2, 3, 4, 5].map((i) => (
            <li key={i}>
              <Skeleton className="h-48 rounded-card" />
            </li>
          ))}
        </ul>
      ) : library.isError ? (
        <EmptyState title="Could not load the library" description={library.error.message} actions={<Button onClick={() => void library.refetch()}>Try again</Button>} />
      ) : items.length === 0 ? (
        <EmptyState
          icon={<BookOpen aria-hidden="true" />}
          title={filtering ? "No paper matches" : "Your library is empty"}
          description={
            filtering
              ? "Try another search or read filter."
              : "Swipe right or save papers in your deck and they land here, ready to cite."
          }
        />
      ) : (
        <>
          <ul className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
            {items.map((item, index) => (
              <LibraryCard key={item.paper.id} item={item} index={index} onOpen={() => sheet.openPaper(item.paper.id)} />
            ))}
          </ul>
          <div ref={sentinel} className="h-px" aria-hidden="true" />
          {hasNextPage ? (
            <div className="mt-6 flex justify-center">
              <Button onClick={() => void fetchNextPage()} loading={isFetchingNextPage}>
                Load more
              </Button>
            </div>
          ) : null}
        </>
      )}
      <PaperDetailSheet paperId={sheet.paperId} projectId={projectId} source="library" onClose={sheet.closePaper} />
    </section>
  );
}
