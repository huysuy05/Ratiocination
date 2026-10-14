/**
 * Knowledge graph (UI.md §9). Without a paper: the project graph (seeds, relevant and saved papers
 * with their shared topics, citations, similarity and authors). With `/graph/:paperId`: that paper's
 * neighbourhood (references, citing papers, similar papers, topics, authors and the authors' most
 * disruptive works), growing as citations load. Graph view (Cytoscape) or list view (accessible;
 * default on phones), relation filters, legend and a selection panel.
 */
import { AnimatePresence } from "motion/react";
import { ArrowLeft, Info, List, Network, Share2 } from "lucide-react";
import { useMemo, useState } from "react";
import { Link, useNavigate } from "react-router";
import { toast } from "sonner";
import { errorMessage } from "../api/client";
import { useExpandPaper, useNeighborhood, useProject, useProjectGraph } from "../api/hooks";
import type { GraphEdge, GraphGroup, GraphNode, Relation } from "../api/types";
import { usePaperIdParam, usePaperSheetParam, useProjectIdParam } from "../app/params";
import { paths } from "../app/paths";
import { useMediaQuery } from "../app/shell/useMediaQuery";
import { Button } from "../components/ui/Button";
import { ToggleChip } from "../components/ui/Chip";
import { EmptyState } from "../components/ui/EmptyState";
import { PageHeader } from "../components/ui/PageHeader";
import { SegmentedControl } from "../components/ui/SegmentedControl";
import { Skeleton } from "../components/ui/Skeleton";
import { Spinner } from "../components/ui/Spinner";
import { GraphCanvas } from "../features/graph/GraphCanvas";
import { GraphLegend } from "../features/graph/GraphLegend";
import { GraphList } from "../features/graph/GraphList";
import { RELATIONS, RELATION_TEXT, relationCounts, workPaperId } from "../features/graph/model";
import { SelectionPanel } from "../features/graph/SelectionPanel";
import { PaperDetailSheet } from "../features/paper/PaperDetailSheet";
import { formatRelativeDate } from "../lib/format";

type View = "graph" | "list";

const EMPTY_NODES: GraphNode[] = [];
const EMPTY_EDGES: GraphEdge[] = [];
const EMPTY_GROUPS: GraphGroup[] = [];

export default function GraphPage() {
  const projectId = useProjectIdParam();
  const paperId = usePaperIdParam();
  const project = useProject(projectId);
  const neighborhood = useNeighborhood(paperId, projectId, { enabled: paperId !== null });
  const projectGraph = useProjectGraph(projectId, { enabled: paperId === null });
  const expand = useExpandPaper();
  const sheet = usePaperSheetParam();
  const navigate = useNavigate();
  const wide = useMediaQuery("(min-width: 768px)");
  const [viewChoice, setViewChoice] = useState<View | null>(null);
  const view: View = viewChoice ?? (wide ? "graph" : "list");
  const [hidden, setHidden] = useState<ReadonlySet<Relation>>(new Set());
  const [selected, setSelected] = useState<string | null>(null);

  const query = paperId !== null ? neighborhood : projectGraph;
  const nodes = query.data?.nodes ?? EMPTY_NODES;
  const edges = query.data?.edges ?? EMPTY_EDGES;
  const groups = query.data?.groups ?? EMPTY_GROUPS;
  const notices = query.data?.notices ?? [];
  const counts = useMemo(() => relationCounts(edges), [edges]);
  const byId = useMemo(() => new Map(nodes.map((node) => [node.id, node])), [nodes]);
  const labelOf = (id: string) => byId.get(id)?.label ?? id;
  const center = nodes.find((node) => node.is_center) ?? null;
  const expansion = paperId !== null ? (neighborhood.data?.expansion ?? null) : null;
  const expanding = expansion?.status === "queued" || expansion?.status === "running" || expand.isPending;

  if (projectId === null) return null;
  const centerOn = (id: number) => {
    setSelected(null);
    void navigate(paths.graph(projectId, id));
  };
  const loadCitations = (force = false) => {
    if (paperId === null) return;
    expand.mutate(
      { paperId, projectId, force },
      {
        onSuccess: (result) => {
          if (!result.job) toast(result.message ?? "Citations are up to date");
        },
        onError: (error) => toast.error(errorMessage(error, "Could not load citations")),
      },
    );
  };
  const toggleRelation = (relation: Relation, on: boolean) =>
    setHidden((current) => {
      const next = new Set(current);
      if (on) next.delete(relation);
      else next.add(relation);
      return next;
    });

  const selectedNode = selected ? (byId.get(selected) ?? null) : null;
  const selectedEdge = selected && !selectedNode ? (edges.find((edge) => edge.id === selected) ?? null) : null;

  let body;
  if (query.isPending) {
    body = <Skeleton className="h-[max(440px,calc(100dvh-360px))] rounded-card" />;
  } else if (query.isError) {
    body = <EmptyState title="Could not load the graph" description={query.error.message} actions={<Button onClick={() => void query.refetch()}>Try again</Button>} />;
  } else if (nodes.length === 0) {
    body = (
      <EmptyState
        size="lg"
        icon={<Share2 aria-hidden="true" />}
        title="Nothing to connect yet"
        description="Add seed papers or mark papers relevant in your deck; their citations, topics and authors appear here."
        actions={
          <Button asChild variant="primary">
            <Link to={paths.feed(projectId)}>Go to the deck</Link>
          </Button>
        }
      />
    );
  } else if (view === "list") {
    body = <GraphList nodes={nodes} edges={edges} groups={groups} onOpen={sheet.openPaper} onCenter={centerOn} />;
  } else {
    const visibleEdges = edges.filter((edge) => !hidden.has(edge.relation));
    body = (
      <div className="flex flex-col gap-3">
        <div className="relative h-[max(440px,calc(100dvh-360px))] overflow-hidden rounded-card border border-border bg-[radial-gradient(circle_at_50%_40%,color-mix(in_oklab,var(--accent)_7%,var(--surface)),var(--surface))] elevation-card">
          <GraphCanvas
            nodes={nodes}
            edges={edges}
            center={paperId !== null ? (neighborhood.data?.center ?? null) : null}
            groups={groups}
            hiddenRelations={hidden}
            selectedId={selected}
            onSelect={setSelected}
            onActivate={(id) => {
              const target = workPaperId(id);
              if (target !== null) centerOn(target);
            }}
          />
          <div className="pointer-events-none absolute inset-y-3 right-3 flex items-start">
            <AnimatePresence>
              {selected ? (
                <SelectionPanel
                  key={selected}
                  node={selectedNode}
                  edge={selectedEdge}
                  labelOf={labelOf}
                  projectId={projectId}
                  onOpen={sheet.openPaper}
                  onCenter={centerOn}
                  onClose={() => setSelected(null)}
                />
              ) : null}
            </AnimatePresence>
          </div>
          {expanding ? (
            <div role="status" className="pointer-events-none absolute top-3 left-3 flex items-center gap-2.5 rounded-pill border border-border bg-surface/90 px-3.5 py-2 text-[12.5px] text-muted backdrop-blur-md">
              <Spinner className="size-4 text-accent" />
              {expansion?.message ?? "Loading citations from Semantic Scholar and OpenAlex…"}
            </div>
          ) : null}
          <p className="sr-only" aria-live="polite">
            {`${nodes.length} nodes and ${visibleEdges.length} links shown. The list view lists the same graph.`}
          </p>
        </div>
        <GraphLegend />
      </div>
    );
  }

  return (
    <section aria-labelledby="page-title" className="mx-auto w-full max-w-7xl">
      <PageHeader
        eyebrow={project.data?.name}
        title={paperId !== null ? (center?.label ?? "Paper graph") : "Project graph"}
        description={
          paperId !== null
            ? "References, citing papers, similar work, topics and authors of this paper. Double-click a paper to centre on it."
            : "Your seeds and the papers you liked, linked by citations, shared topics, similarity and shared authors."
        }
        actions={
          <>
            {paperId !== null ? (
              <Button asChild variant="ghost" leadingIcon={<ArrowLeft aria-hidden="true" />}>
                <Link to={paths.graph(projectId)}>Project graph</Link>
              </Button>
            ) : null}
            {paperId !== null ? (
              <Button leadingIcon={<Network aria-hidden="true" />} loading={expanding} onClick={() => loadCitations(expansion?.status === "done")}>
                {expanding ? "Loading citations…" : expansion?.status === "done" ? "Refresh citations" : "Load citations"}
              </Button>
            ) : null}
          </>
        }
      />
      <div className="mb-4 flex flex-wrap items-center gap-2">
        <SegmentedControl
          label="View"
          size="sm"
          value={view}
          onValueChange={(value) => setViewChoice(value)}
          options={[
            { value: "graph", label: "Graph", icon: <Share2 aria-hidden="true" /> },
            { value: "list", label: "List", icon: <List aria-hidden="true" /> },
          ]}
        />
        {view === "graph" && nodes.length > 0 ? (
          <div className="flex flex-wrap gap-1.5" role="group" aria-label="Show relations">
            {RELATIONS.filter((relation) => counts[relation] > 0).map((relation) => (
              <ToggleChip
                key={relation}
                selected={!hidden.has(relation)}
                onSelectedChange={(on) => toggleRelation(relation, on)}
                color={RELATION_TEXT[relation].color}
                count={counts[relation]}
              >
                {RELATION_TEXT[relation].label}
              </ToggleChip>
            ))}
          </div>
        ) : null}
        {expansion?.last_expanded_at ? (
          <span className="ml-auto text-[12.5px] text-faint">Citations loaded {formatRelativeDate(expansion.last_expanded_at)}</span>
        ) : null}
      </div>
      {notices.length > 0 ? (
        <div role="status" className="mb-4 flex items-start gap-2.5 rounded-panel border border-border bg-surface-2/60 px-4 py-3 text-[13px] text-muted">
          <Info aria-hidden="true" className="mt-0.5 size-4 shrink-0 text-accent" />
          <ul className="flex flex-col gap-1">
            {notices.map((notice) => (
              <li key={notice}>{notice}</li>
            ))}
          </ul>
        </div>
      ) : null}
      {body}
      <PaperDetailSheet paperId={sheet.paperId} projectId={projectId} source="graph" onClose={sheet.closePaper} />
    </section>
  );
}
