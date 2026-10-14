/**
 * New project wizard (UI.md §10): Basics → Sources → Seed papers → Your draft → Launch. The project
 * is created as a draft (`active: false`) when leaving Sources; `?project=&step=` keeps the draft
 * across reloads. Launching activates it, which queues the first deck build, and opens the feed.
 */
import clsx from "clsx";
import { AnimatePresence, motion } from "motion/react";
import { ArrowLeft, ArrowRight, Check, Rocket } from "lucide-react";
import { useEffect, useRef, useState, type ReactNode } from "react";
import { useNavigate, useSearchParams } from "react-router";
import { toast } from "sonner";
import { errorMessage, getFieldErrors } from "../api/client";
import { parseId, useCreateProject, useDeleteProject, useDocuments, useProject, useSeeds, useUpdateProject } from "../api/hooks";
import type { ProjectSettings } from "../api/types";
import { paths } from "../app/paths";
import { Button } from "../components/ui/Button";
import { Card } from "../components/ui/Card";
import { ConfirmDialog } from "../components/ui/ConfirmDialog";
import { Field, Input, Textarea } from "../components/ui/Input";
import { PageHeader } from "../components/ui/PageHeader";
import { Slider } from "../components/ui/Slider";
import { DocumentDropzone, DocumentList } from "../features/documents/DocumentTools";
import { categoryLabel } from "../features/projects/categories";
import { CategoryPicker } from "../features/projects/CategoryPicker";
import { KeywordInput } from "../features/projects/KeywordInput";
import { SeedAdder, SeedList } from "../features/projects/SeedTools";
import { SPRING, useMotionPreference } from "../lib/motion";

const STEPS = ["Basics", "Sources", "Seed papers", "Your draft", "Launch"] as const;
type Step = 0 | 1 | 2 | 3 | 4;

function StepIndicator({ step }: { step: Step }) {
  return (
    <ol className="mb-8 flex items-center gap-2" aria-label="Wizard steps">
      {STEPS.map((label, index) => {
        const done = index < step;
        const current = index === step;
        return (
          <li key={label} className="flex min-w-0 flex-1 items-center gap-2" aria-current={current ? "step" : undefined}>
            <span
              className={clsx(
                "grid size-7 shrink-0 place-items-center rounded-full text-[12px] font-semibold transition-colors duration-200",
                done && "bg-accent text-on-accent",
                current && "bg-accent/15 text-accent ring-2 ring-accent",
                !done && !current && "bg-surface-2 text-faint ring-1 ring-border",
              )}
            >
              {done ? <Check aria-hidden="true" className="size-3.5" strokeWidth={3} /> : index + 1}
            </span>
            <span className={clsx("hidden truncate text-[12.5px] font-medium sm:block", current ? "text-text" : "text-muted")}>{label}</span>
            {index < STEPS.length - 1 ? (
              <span className="relative h-px min-w-3 flex-1 bg-border">
                <motion.span
                  className="absolute inset-y-0 left-0 bg-accent"
                  initial={false}
                  animate={{ width: done ? "100%" : "0%" }}
                  transition={SPRING}
                />
              </span>
            ) : null}
          </li>
        );
      })}
    </ol>
  );
}

function StepPanel({ title, description, children }: { title: string; description: ReactNode; children: ReactNode }) {
  return (
    <Card padding="lg" className="flex flex-col gap-6">
      <header className="flex flex-col gap-1">
        <h2 className="text-[19px] font-semibold text-text">{title}</h2>
        <p className="text-[14px] text-muted">{description}</p>
      </header>
      {children}
    </Card>
  );
}

function fieldMap(error: unknown): Record<string, string> {
  const out: Record<string, string> = {};
  for (const field of getFieldErrors(error)) if (!(field.path in out)) out[field.path] = field.message;
  return out;
}

export default function NewProjectPage() {
  const [params, setParams] = useSearchParams();
  const navigate = useNavigate();
  const { reduced } = useMotionPreference();
  const draftId = parseId(params.get("project"));
  const stepParam = Number(params.get("step"));
  const step: Step = draftId !== null && [1, 2, 3, 4].includes(stepParam) ? (stepParam as Step) : draftId !== null ? 2 : 0;
  const [localStep, setLocalStep] = useState<Step>(0);
  const current: Step = draftId === null ? (localStep > 1 ? 1 : localStep) : step;
  const direction = useRef(1);

  const [name, setName] = useState("");
  const [question, setQuestion] = useState("");
  const [description, setDescription] = useState("");
  const [keywords, setKeywords] = useState<string[]>([]);
  const [categories, setCategories] = useState<string[]>([]);
  const [windowDays, setWindowDays] = useState(14);
  const [deckSize, setDeckSize] = useState(20);
  const [basicsTried, setBasicsTried] = useState(false);
  const [confirmCancel, setConfirmCancel] = useState(false);

  const draft = useProject(draftId);
  const seeds = useSeeds(draftId);
  const documents = useDocuments(draftId);
  const create = useCreateProject();
  const update = useUpdateProject(draftId);
  const remove = useDeleteProject();
  const prefilled = useRef(false);

  // Resuming a draft from the URL: load its fields once.
  useEffect(() => {
    const project = draft.data;
    if (!project || prefilled.current) return;
    prefilled.current = true;
    if (project.active) {
      void navigate(paths.feed(project.id), { replace: true });
      return;
    }
    setName(project.name);
    setQuestion(project.research_question);
    setDescription(project.description);
    setKeywords(project.keywords ?? []);
    setCategories(project.settings.arxiv_categories);
    setWindowDays(project.settings.time_window_days);
    setDeckSize(project.settings.slate_size);
  }, [draft.data, navigate]);

  const goTo = (next: Step, projectId: number | null = draftId) => {
    direction.current = next >= current ? 1 : -1;
    if (projectId === null) {
      setLocalStep(next);
      return;
    }
    // Creating the draft pushes a history entry; moving between later steps replaces it.
    setParams({ project: String(projectId), step: String(next) }, { replace: draftId !== null });
  };

  const settings: ProjectSettings = { arxiv_categories: categories, time_window_days: windowDays, slate_size: deckSize };
  const saveError = fieldMap(create.error ?? update.error);
  const keywordsError = saveError.keywords ?? Object.entries(saveError).find(([path]) => path.startsWith("keywords."))?.[1] ?? null;
  const nameMissing = basicsTried && !name.trim();
  const questionMissing = basicsTried && !question.trim();

  const nextFromBasics = () => {
    setBasicsTried(true);
    if (!name.trim() || !question.trim()) return;
    goTo(1);
  };

  const nextFromSources = () => {
    const body = { name: name.trim(), research_question: question.trim(), description: description.trim(), keywords, settings };
    if (draftId === null) {
      create.mutate({ ...body, active: false }, { onSuccess: (project) => goTo(2, project.id) });
    } else {
      update.mutate(body, { onSuccess: () => goTo(2) });
    }
  };

  const launch = () => {
    update.mutate(
      { active: true },
      {
        onSuccess: (project) => {
          toast.success("Your project is live — building the first deck");
          void navigate(paths.feed(project.id));
        },
        onError: (error) => toast.error(errorMessage(error, "Could not launch the project")),
      },
    );
  };

  const cancel = () => {
    if (draftId === null) {
      void navigate(paths.projects());
      return;
    }
    setConfirmCancel(true);
  };

  const offset = reduced ? 0 : 36 * direction.current;
  let panel: ReactNode;
  switch (current) {
    case 0:
      panel = (
        <StepPanel title="What are you working on?" description="The research question is the heart of your project: new papers are ranked by how close they are to it.">
          <Field label="Project name" required error={nameMissing ? "Give the project a name." : saveError.name}>
            <Input autoFocus maxLength={200} value={name} onChange={(event) => setName(event.target.value)} placeholder="e.g. Sparse retrieval for long documents" />
          </Field>
          <Field label="Research question" required error={questionMissing ? "Write your research question." : saveError.research_question}>
            <Textarea
              rows={4}
              maxLength={4000}
              value={question}
              onChange={(event) => setQuestion(event.target.value)}
              placeholder="e.g. How can sparse retrieval models handle documents longer than their context window without losing recall?"
              className="font-serif text-[15px]"
            />
          </Field>
          <KeywordInput value={keywords} onChange={setKeywords} error={keywordsError} />
          <Field label="Description" hint="Optional — methods, datasets or constraints that matter to you." error={saveError.description}>
            <Textarea rows={3} maxLength={4000} value={description} onChange={(event) => setDescription(event.target.value)} />
          </Field>
        </StepPanel>
      );
      break;
    case 1:
      panel = (
        <StepPanel title="Where should it look?" description="New papers come from these arXiv listings every day. Pick the ones your field publishes in.">
          <CategoryPicker value={categories} onChange={setCategories} error={saveError["settings.arxiv_categories"] ?? null} />
          <div className="grid gap-6 sm:grid-cols-2">
            <Slider
              label="Time window"
              value={windowDays}
              onValueChange={setWindowDays}
              min={1}
              max={90}
              step={1}
              formatValue={(v) => `${v} day${v === 1 ? "" : "s"}`}
              description="How far back the deck looks for new papers."
            />
            <Slider
              label="Cards per deck"
              value={deckSize}
              onValueChange={setDeckSize}
              min={5}
              max={50}
              step={1}
              formatValue={(v) => `${v} papers`}
              description="A short deck you finish beats a long one you abandon."
            />
          </div>
        </StepPanel>
      );
      break;
    case 2:
      panel = (
        <StepPanel title="Papers you already trust" description="Optional. Seed papers anchor the deck: papers similar to them, citing them or cited by them rank higher.">
          {draftId !== null ? (
            <>
              <SeedAdder projectId={draftId} />
              <SeedList projectId={draftId} />
            </>
          ) : null}
        </StepPanel>
      );
      break;
    case 3:
      panel = (
        <StepPanel title="Your draft" description="Optional. Upload a draft or proposal and new papers are matched against each of its sections.">
          {draftId !== null ? (
            <>
              <DocumentDropzone projectId={draftId} compact />
              <DocumentList projectId={draftId} />
            </>
          ) : null}
        </StepPanel>
      );
      break;
    default:
      panel = (
        <StepPanel title="Ready to launch" description="Ratiocination will fetch recent papers from your categories and build your first deck. It takes a minute or two.">
          <dl className="grid gap-4 rounded-panel border border-border bg-surface-2/50 p-5 text-[14px] sm:grid-cols-2">
            <div className="sm:col-span-2">
              <dt className="text-eyebrow">Question</dt>
              <dd className="mt-1 font-serif text-[15px] leading-relaxed text-text">{question}</dd>
            </div>
            {keywords.length > 0 ? (
              <div className="sm:col-span-2">
                <dt className="text-eyebrow">Keywords</dt>
                <dd className="mt-1.5 flex flex-wrap gap-1.5">
                  {keywords.map((keyword) => (
                    <span key={keyword} className="rounded-pill border border-border px-2.5 py-0.5 text-[12.5px] text-muted">
                      {keyword}
                    </span>
                  ))}
                </dd>
              </div>
            ) : null}
            <div className="sm:col-span-2">
              <dt className="text-eyebrow">Categories</dt>
              <dd className="mt-1.5 flex flex-wrap gap-1.5">
                {categories.length > 0 ? (
                  categories.map((code) => (
                    <span key={code} className="rounded-pill border border-border px-2.5 py-0.5 text-[12.5px] text-muted">
                      {categoryLabel(code)}
                    </span>
                  ))
                ) : (
                  <span className="text-not-relevant">None — add at least one category to get a deck.</span>
                )}
              </dd>
            </div>
            <div>
              <dt className="text-eyebrow">Window and deck</dt>
              <dd className="mt-1 text-text">
                Last {windowDays} days · {deckSize} cards
              </dd>
            </div>
            <div>
              <dt className="text-eyebrow">Anchors</dt>
              <dd className="mt-1 text-text">
                {seeds.data?.length ?? 0} seed papers · {documents.data?.length ?? 0} documents
              </dd>
            </div>
          </dl>
        </StepPanel>
      );
  }

  const busy = create.isPending || update.isPending;
  return (
    <section aria-labelledby="page-title" className="mx-auto w-full max-w-3xl">
      <PageHeader title="New project" description="Five quick steps to a daily deck tuned to your research." />
      <StepIndicator step={current} />
      <AnimatePresence mode="wait" initial={false}>
        <motion.div
          key={current}
          initial={{ opacity: 0, x: offset }}
          animate={{ opacity: 1, x: 0 }}
          exit={{ opacity: 0, x: -offset }}
          transition={{ duration: 0.22, ease: "easeOut" }}
        >
          {panel}
        </motion.div>
      </AnimatePresence>
      <div className="mt-6 flex flex-wrap items-center justify-between gap-3">
        <Button variant="ghost" onClick={cancel}>
          {draftId === null ? "Cancel" : "Discard draft"}
        </Button>
        <div className="flex flex-wrap items-center gap-2">
          {current > 0 ? (
            <Button leadingIcon={<ArrowLeft aria-hidden="true" />} onClick={() => goTo((current - 1) as Step)} disabled={busy}>
              Back
            </Button>
          ) : null}
          {current === 0 ? (
            <Button variant="primary" trailingIcon={<ArrowRight aria-hidden="true" />} onClick={nextFromBasics}>
              Next
            </Button>
          ) : current === 1 ? (
            <Button variant="primary" trailingIcon={<ArrowRight aria-hidden="true" />} onClick={nextFromSources} loading={busy}>
              {draftId === null ? "Create draft" : "Save and continue"}
            </Button>
          ) : current < 4 ? (
            <Button variant="primary" trailingIcon={<ArrowRight aria-hidden="true" />} onClick={() => goTo((current + 1) as Step)}>
              {current === 2 ? (seeds.data?.length ? "Next" : "Skip for now") : documents.data?.length ? "Next" : "Skip for now"}
            </Button>
          ) : (
            <Button variant="primary" size="lg" leadingIcon={<Rocket aria-hidden="true" />} onClick={launch} loading={busy}>
              Launch my project
            </Button>
          )}
        </div>
      </div>
      {current === 1 && (["name", "research_question", "description"].some((key) => key in saveError) || keywordsError) ? (
        <p role="alert" className="mt-3 text-right text-[13px] text-not-relevant">
          Check the Basics step: {keywordsError ?? saveError.name ?? saveError.research_question ?? saveError.description}
        </p>
      ) : null}
      {(create.isError || update.isError) && Object.keys(saveError).length === 0 ? (
        <p role="alert" className="mt-3 text-right text-[13px] text-not-relevant">
          {errorMessage(create.error ?? update.error)}
        </p>
      ) : null}
      <ConfirmDialog
        open={confirmCancel}
        onOpenChange={setConfirmCancel}
        tone="danger"
        title="Discard this draft?"
        description="The draft project, its seed papers and any uploaded documents are deleted."
        confirmLabel="Discard draft"
        pending={remove.isPending}
        onConfirm={() => {
          if (draftId === null) return;
          // Leave first: queries of a deleted project that are still mounted would refetch into 404s.
          void navigate(paths.projects());
          remove.mutate(draftId, { onError: (error) => toast.error(errorMessage(error, "Could not discard the draft")) });
        }}
      />
    </section>
  );
}
