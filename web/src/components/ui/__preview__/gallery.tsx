/**
 * Design-system preview (dev only): `npx vite` then open /src/components/ui/__preview__/gallery.html
 * (or `npm run shots -- --port <p> --routes /src/components/ui/__preview__/gallery.html`).
 * `?scene=dialog|confirm|sheet|menu|tooltip` opens an overlay for screenshots. Not bundled.
 */
import "@fontsource-variable/inter";
import "@fontsource-variable/source-serif-4";
import "../../../styles/globals.css";

import {
  Bookmark,
  Check,
  Compass,
  Dices,
  FileText,
  Layers,
  Pin,
  Plus,
  RotateCw,
  Search,
  Sparkles,
  Trash2,
  Undo2,
  X,
} from "lucide-react";
import { MotionConfig } from "motion/react";
import { StrictMode, useState, type ReactNode } from "react";
import { createRoot } from "react-dom/client";
import { MotionPreferenceProvider, useMotionPreference } from "../../../lib/motion";
import { ThemeProvider, useTheme, type ThemePreference } from "../../../lib/theme";
import {
  Badge,
  BrandMark,
  Button,
  Card,
  CardDescription,
  CardHeader,
  CardTitle,
  Chip,
  ConfirmDialog,
  Dialog,
  EmptyState,
  Field,
  IconButton,
  Input,
  Kbd,
  Menu,
  MenuContent,
  MenuItem,
  MenuLabel,
  MenuSeparator,
  MenuTrigger,
  ProgressBar,
  RadarBackdrop,
  RadarSweep,
  SegmentedControl,
  Sheet,
  Skeleton,
  SkeletonText,
  Slider,
  Switch,
  Tabs,
  TabsContent,
  TabsList,
  TabsTrigger,
  ToggleChip,
  Tooltip,
  TooltipProvider,
} from "..";

const FEATURES = ["question", "anchor", "draft", "citation", "preference", "recency"] as const;
const STATES = ["relevant", "not-relevant", "save", "read", "disruptive", "consolidating", "authored"] as const;
const EDGES = ["edge-cites", "edge-similar", "edge-topic", "edge-authored"] as const;

function Section({ title, children, className }: { title: string; children: ReactNode; className?: string }) {
  return (
    <Card padding="md" className={className}>
      <CardHeader>
        <CardTitle as="h2">{title}</CardTitle>
      </CardHeader>
      <div className="mt-4 flex flex-col gap-4">{children}</div>
    </Card>
  );
}

function Row({ children }: { children: ReactNode }) {
  return <div className="flex flex-wrap items-center gap-2">{children}</div>;
}

function Swatches({ names, prefix = "" }: { names: readonly string[]; prefix?: string }) {
  return (
    <div className="flex flex-wrap gap-2">
      {names.map((name) => (
        <div key={name} className="flex flex-col items-center gap-1">
          <span className="size-9 rounded-control border border-border" style={{ background: `var(--${prefix}${name})` }} />
          <span className="text-[11px] text-muted">{name}</span>
        </div>
      ))}
    </div>
  );
}

function Scenes({ scene }: { scene: string | null }) {
  const [dialogOpen, setDialogOpen] = useState(scene === "dialog");
  const [confirmOpen, setConfirmOpen] = useState(scene === "confirm");
  const [sheetOpen, setSheetOpen] = useState(scene === "sheet");
  return (
    <>
      <Dialog
        open={dialogOpen}
        onOpenChange={setDialogOpen}
        icon={<Sparkles aria-hidden="true" />}
        title="Rebuild today's deck"
        description="Ratiocination re-ranks new papers with your latest feedback. This takes about a minute."
        footer={
          <>
            <Button variant="ghost" onClick={() => setDialogOpen(false)}>
              Cancel
            </Button>
            <Button variant="primary" leadingIcon={<RotateCw aria-hidden="true" />}>
              Rebuild deck
            </Button>
          </>
        }
      >
        <Field label="Window" hint="Papers published in the last N days.">
          <Input defaultValue="14 days" />
        </Field>
      </Dialog>
      <ConfirmDialog
        open={confirmOpen}
        onOpenChange={setConfirmOpen}
        tone="danger"
        title="Delete this document?"
        description="Its text, sections, chunks and embeddings are removed from this machine."
        confirmLabel="Delete document"
        onConfirm={() => undefined}
      >
        <ul className="list-disc space-y-1 pl-5 text-muted">
          <li>148 chunks and their vectors</li>
          <li>Evidence links in 6 explanations</li>
        </ul>
      </ConfirmDialog>
      <Sheet
        open={sheetOpen}
        onOpenChange={setSheetOpen}
        title="Sample paper 07: Calibrated selection for daily reading lists"
        description="arXiv 2610.01234 · Oct 11, 2026"
        footer={
          <div className="flex items-center justify-between gap-2">
            <Button variant="danger-soft" leadingIcon={<X aria-hidden="true" />}>
              Not relevant
            </Button>
            <div className="flex gap-2">
              <IconButton label="Pin" icon={<Pin />} variant="secondary" shortcut="P" />
              <Button variant="primary" leadingIcon={<Check aria-hidden="true" />}>
                Relevant
              </Button>
            </div>
          </div>
        }
      >
        <p className="paper-abstract text-muted">
          We study conformal selection for recommending research papers with a precision guarantee. Sample abstract text for the
          design-system preview; real abstracts appear in the paper sheet.
        </p>
        <div className="mt-6 flex flex-col gap-3">
          <SkeletonText lines={4} />
        </div>
      </Sheet>
      <div className="flex flex-wrap gap-2">
        <Button onClick={() => setDialogOpen(true)}>Open dialog</Button>
        <Button variant="danger-soft" onClick={() => setConfirmOpen(true)}>
          Open confirm
        </Button>
        <Button variant="outline" onClick={() => setSheetOpen(true)}>
          Open sheet
        </Button>
        <Menu defaultOpen={scene === "menu"}>
          <MenuTrigger asChild>
            <Button variant="secondary">Open menu</Button>
          </MenuTrigger>
          <MenuContent>
            <MenuLabel>Card actions</MenuLabel>
            <MenuItem icon={<Bookmark />} shortcut="S">
              Save
            </MenuItem>
            <MenuItem icon={<Layers />} shortcut="D">
              Dismiss v3
            </MenuItem>
            <MenuSeparator />
            <MenuItem icon={<Trash2 />} tone="danger">
              Hide topic “Graph learning”
            </MenuItem>
          </MenuContent>
        </Menu>
        <Tooltip content="Undo the last decision" shortcut="Z" defaultOpen={scene === "tooltip"}>
          <Button variant="ghost" leadingIcon={<Undo2 aria-hidden="true" />}>
            Hover for tooltip
          </Button>
        </Tooltip>
      </div>
    </>
  );
}

function Gallery() {
  const scene = new URLSearchParams(window.location.search).get("scene");
  const { theme, setTheme } = useTheme();
  const { reduced } = useMotionPreference();
  const [tab, setTab] = useState("saved");
  const [segment, setSegment] = useState<"list" | "graph">("graph");
  const [switchOn, setSwitchOn] = useState(true);
  const [slider, setSlider] = useState(0.75);
  const [chips, setChips] = useState({ cites: true, similar: true, topic: false });
  return (
    <MotionConfig reducedMotion={reduced ? "always" : "never"}>
      <TooltipProvider>
        <div className="relative isolate min-h-dvh bg-bg px-4 py-8 text-text md:px-10">
          <div aria-hidden="true" className="pointer-events-none absolute inset-x-0 top-0 -z-10 h-[560px] app-backdrop" />
          <header className="mx-auto mb-8 flex max-w-6xl flex-wrap items-center justify-between gap-4">
            <div className="flex items-center gap-3">
              <BrandMark size={36} />
              <div>
                <h1 className="text-xl font-semibold tracking-[-0.02em]">Ratiocination design system</h1>
                <p className="text-sm text-muted">components/ui · tokens.css · globals.css</p>
              </div>
            </div>
            <SegmentedControl<ThemePreference>
              label="Theme"
              value={theme}
              onValueChange={setTheme}
              options={[
                { value: "dark", label: "Dark" },
                { value: "light", label: "Light" },
                { value: "system", label: "System" },
              ]}
            />
          </header>
          <main className="mx-auto grid max-w-6xl gap-5 md:grid-cols-2">
            <Section title="Buttons">
              <Row>
                <Button variant="primary" leadingIcon={<Check aria-hidden="true" />}>
                  Relevant
                </Button>
                <Button>Secondary</Button>
                <Button variant="outline">Outline</Button>
                <Button variant="ghost">Ghost</Button>
              </Row>
              <Row>
                <Button variant="danger">Delete project</Button>
                <Button variant="danger-soft" leadingIcon={<X aria-hidden="true" />}>
                  Not relevant
                </Button>
                <Button variant="primary" loading>
                  Building…
                </Button>
                <Button disabled>Disabled</Button>
              </Row>
              <Row>
                <Button size="sm" variant="secondary" leadingIcon={<Plus aria-hidden="true" />}>
                  Small
                </Button>
                <Button size="lg" variant="primary">
                  Build my first deck
                </Button>
              </Row>
              <Row>
                <IconButton label="Not relevant" icon={<X />} variant="secondary" shape="circle" size="lg" shortcutKey="ArrowLeft" />
                <IconButton label="Undo" icon={<Undo2 />} variant="secondary" shape="circle" shortcut="Z" />
                <IconButton label="Save" icon={<Bookmark />} variant="secondary" shape="circle" size="lg" shortcut="S" />
                <IconButton label="Pin" icon={<Pin />} variant="secondary" shape="circle" shortcut="P" />
                <IconButton label="Relevant" icon={<Check />} variant="primary" shape="circle" size="lg" shortcutKey="ArrowRight" />
                <IconButton label="Rebuild" icon={<RotateCw />} />
              </Row>
            </Section>

            <Section title="Badges & chips">
              <Row>
                <Badge tone="accent" variant="soft" glow icon={<Sparkles aria-hidden="true" />}>
                  Close to your question
                </Badge>
                <Badge tone="disruptive" icon={<Compass aria-hidden="true" />}>
                  Disruptive
                </Badge>
                <Badge tone="consolidating" icon={<Dices aria-hidden="true" />}>
                  Consolidating
                </Badge>
                <Badge tone="not-relevant" variant="solid">
                  Retracted
                </Badge>
                <Badge>cs.LG</Badge>
              </Row>
              <Row>
                {STATES.map((state) => (
                  <Badge key={state} tone={state} variant="soft" size="md">
                    {state}
                  </Badge>
                ))}
              </Row>
              <Row>
                <Chip>stat.ML</Chip>
                <Chip tone="accent" onRemove={() => undefined} removeLabel="Remove cs.LG">
                  cs.LG
                </Chip>
                <Chip icon={<FileText aria-hidden="true" />} tone="accent">
                  draft.pdf
                </Chip>
                <ToggleChip selected={chips.cites} onSelectedChange={(selected) => setChips({ ...chips, cites: selected })} color="var(--edge-cites)" count={42}>
                  cites
                </ToggleChip>
                <ToggleChip selected={chips.similar} onSelectedChange={(selected) => setChips({ ...chips, similar: selected })} color="var(--edge-similar)">
                  similar (inferred)
                </ToggleChip>
                <ToggleChip selected={chips.topic} onSelectedChange={(selected) => setChips({ ...chips, topic: selected })} color="var(--edge-topic)">
                  topic (OpenAlex)
                </ToggleChip>
              </Row>
              <Row>
                <span className="text-sm text-muted">Shortcuts</span>
                <Kbd keyName="ArrowLeft" />
                <Kbd keyName="ArrowRight" />
                <Kbd keyName="ArrowUp" />
                <Kbd>S</Kbd>
                <Kbd>P</Kbd>
                <Kbd keyName="Escape" />
              </Row>
            </Section>

            <Section title="Tabs, segmented, switch, slider">
              <Tabs value={tab} onValueChange={setTab}>
                <TabsList aria-label="Library">
                  <TabsTrigger value="saved" badge={12}>
                    Saved
                  </TabsTrigger>
                  <TabsTrigger value="pinned" badge={3}>
                    Pinned
                  </TabsTrigger>
                  <TabsTrigger value="relevant" badge={48}>
                    Relevant
                  </TabsTrigger>
                  <TabsTrigger value="read">Read</TabsTrigger>
                </TabsList>
                <TabsContent value="saved" className="text-sm text-muted">
                  12 saved papers
                </TabsContent>
                <TabsContent value="pinned" className="text-sm text-muted">
                  3 pinned papers
                </TabsContent>
                <TabsContent value="relevant" className="text-sm text-muted">
                  48 relevant papers
                </TabsContent>
                <TabsContent value="read" className="text-sm text-muted">
                  Read papers
                </TabsContent>
              </Tabs>
              <Tabs defaultValue="overview" variant="underline">
                <TabsList aria-label="Sections">
                  <TabsTrigger value="overview">Overview</TabsTrigger>
                  <TabsTrigger value="evidence">Evidence</TabsTrigger>
                  <TabsTrigger value="versions">Versions</TabsTrigger>
                </TabsList>
              </Tabs>
              <SegmentedControl
                label="View"
                value={segment}
                onValueChange={setSegment}
                options={[
                  { value: "graph", label: "Graph" },
                  { value: "list", label: "List" },
                ]}
              />
              <Switch
                label="Auto-expansion"
                description="Fetch neighbours of papers you mark relevant or pin."
                checked={switchOn}
                onCheckedChange={setSwitchOn}
              />
              <Slider
                label="Diversity (MMR λ)"
                value={slider}
                min={0}
                max={1}
                step={0.05}
                onValueChange={setSlider}
                formatValue={(value) => value.toFixed(2)}
              />
            </Section>

            <Section title="Progress & loading">
              <ProgressBar value={0.42} label="Deck build" showValue />
              <ProgressBar value={null} label="Fetching topics" />
              <ProgressBar value={0.63} label="OpenAlex budget" tone="save" marker={0.8} markerLabel="Stops at 80%" showValue />
              <div className="flex items-center gap-3">
                <Skeleton shape="circle" className="size-10" />
                <div className="flex flex-1 flex-col gap-2">
                  <Skeleton shape="text" className="w-2/3" />
                  <Skeleton shape="text" className="w-1/3" />
                </div>
              </div>
              <SkeletonText lines={3} />
            </Section>

            <Section title="Radar sweep (job running)">
              <div className="relative overflow-hidden rounded-panel border border-border bg-surface-2/40 py-8">
                <RadarBackdrop />
                <RadarSweep title="Building your deck" message="Embedding 412 papers locally" progress={0.58} label="Deck build progress" />
              </div>
            </Section>

            <Section title="Empty state">
              <EmptyState
                icon={<Search aria-hidden="true" />}
                title="Create your first project"
                description="A project watches arXiv for papers that match your research question and learns from your feedback."
                actions={
                  <Button variant="primary" leadingIcon={<Plus aria-hidden="true" />}>
                    New project
                  </Button>
                }
              />
            </Section>

            <Section title="Fields">
              <Field label="Project name" hint="Shown in the sidebar and on cards.">
                <Input placeholder="e.g. Graph learning for proteins" />
              </Field>
              <Field label="arXiv category" error="Use the form cs.LG or stat.ML.">
                <Input defaultValue="machine learning" />
              </Field>
              <Input leadingIcon={<Search />} placeholder="Search your library" aria-label="Search" />
            </Section>

            <Section title="Palette">
              <p className="text-sm text-muted">States</p>
              <Swatches names={STATES} />
              <p className="text-sm text-muted">Edges</p>
              <Swatches names={EDGES} />
              <p className="text-sm text-muted">Score features (stack order)</p>
              <Swatches names={FEATURES} prefix="feat-" />
              <div className="flex h-3 overflow-hidden rounded-pill">
                {FEATURES.map((feature) => (
                  <span key={feature} className="h-full flex-1 border-r-2 border-surface last:border-r-0" style={{ background: `var(--feat-${feature})` }} />
                ))}
              </div>
            </Section>

            <Section title="Overlays" className="md:col-span-2">
              <Scenes scene={scene} />
              <CardDescription>Dialog, confirm, sheet, menu and tooltip; append ?scene=… to open one on load.</CardDescription>
            </Section>
          </main>
        </div>
      </TooltipProvider>
    </MotionConfig>
  );
}

const container = document.getElementById("root");
if (container) {
  createRoot(container).render(
    <StrictMode>
      <ThemeProvider>
        <MotionPreferenceProvider>
          <Gallery />
        </MotionPreferenceProvider>
      </ThemeProvider>
    </StrictMode>,
  );
}
