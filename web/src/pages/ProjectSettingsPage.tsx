/**
 * Project settings (UI.md §10): basics, sources, seed papers, daily decks on/off and deletion. Each
 * section saves on its own (PATCH sends only that section's keys).
 */
import { Trash2 } from "lucide-react";
import { useEffect, useState, type FormEvent, type ReactNode } from "react";
import { useNavigate } from "react-router";
import { toast } from "sonner";
import { errorMessage, getFieldErrors } from "../api/client";
import { useDeleteProject, useProject, useUpdateProject } from "../api/hooks";
import type { Project } from "../api/types";
import { useProjectIdParam } from "../app/params";
import { paths } from "../app/paths";
import { Button } from "../components/ui/Button";
import { Card } from "../components/ui/Card";
import { ConfirmDialog } from "../components/ui/ConfirmDialog";
import { EmptyState } from "../components/ui/EmptyState";
import { Field, Input, Textarea } from "../components/ui/Input";
import { PageHeader } from "../components/ui/PageHeader";
import { Skeleton } from "../components/ui/Skeleton";
import { Slider } from "../components/ui/Slider";
import { Switch } from "../components/ui/Switch";
import { CategoryPicker } from "../features/projects/CategoryPicker";
import { KeywordInput } from "../features/projects/KeywordInput";
import { SeedAdder, SeedList } from "../features/projects/SeedTools";

function Section({ title, description, children }: { title: string; description?: ReactNode; children: ReactNode }) {
  return (
    <Card padding="lg" className="flex flex-col gap-5">
      <header className="flex flex-col gap-1">
        <h2 className="text-[17px] font-semibold text-text">{title}</h2>
        {description ? <p className="text-[13.5px] text-muted">{description}</p> : null}
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

function BasicsForm({ project }: { project: Project }) {
  const update = useUpdateProject(project.id);
  const [name, setName] = useState(project.name);
  const [question, setQuestion] = useState(project.research_question);
  const [description, setDescription] = useState(project.description);
  const savedKeywords = project.keywords ?? [];
  const [keywords, setKeywords] = useState<string[]>(savedKeywords);
  const dirty =
    name !== project.name ||
    question !== project.research_question ||
    description !== project.description ||
    keywords.join("\n") !== savedKeywords.join("\n");
  const errors = fieldMap(update.error);
  const keywordsError = errors.keywords ?? Object.entries(errors).find(([path]) => path.startsWith("keywords."))?.[1] ?? null;

  const onSubmit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    update.mutate(
      { name: name.trim(), research_question: question.trim(), description: description.trim(), keywords },
      { onSuccess: () => toast.success("Saved — the next deck uses your new question and keywords") },
    );
  };

  return (
    <form onSubmit={onSubmit} className="flex flex-col gap-4">
      <Field label="Project name" required error={errors.name}>
        <Input maxLength={200} value={name} onChange={(event) => setName(event.target.value)} />
      </Field>
      <Field label="Research question" required error={errors.research_question}>
        <Textarea rows={4} maxLength={4000} value={question} onChange={(event) => setQuestion(event.target.value)} className="font-serif text-[15px]" />
      </Field>
      <KeywordInput value={keywords} onChange={setKeywords} error={keywordsError} />
      <Field label="Description" error={errors.description}>
        <Textarea rows={3} maxLength={4000} value={description} onChange={(event) => setDescription(event.target.value)} />
      </Field>
      <div className="flex justify-end">
        <Button type="submit" variant="primary" disabled={!dirty || !name.trim() || !question.trim()} loading={update.isPending}>
          Save basics
        </Button>
      </div>
    </form>
  );
}

function SourcesForm({ project }: { project: Project }) {
  const update = useUpdateProject(project.id);
  const [categories, setCategories] = useState(project.settings.arxiv_categories);
  const [windowDays, setWindowDays] = useState(project.settings.time_window_days);
  const [deckSize, setDeckSize] = useState(project.settings.slate_size);
  const dirty =
    categories.join(",") !== project.settings.arxiv_categories.join(",") ||
    windowDays !== project.settings.time_window_days ||
    deckSize !== project.settings.slate_size;
  const errors = fieldMap(update.error);

  const save = () =>
    update.mutate(
      { settings: { arxiv_categories: categories, time_window_days: windowDays, slate_size: deckSize } },
      { onSuccess: () => toast.success("Saved — rebuilding the deck with the new sources") },
    );

  return (
    <div className="flex flex-col gap-6">
      <CategoryPicker value={categories} onChange={setCategories} error={errors["settings.arxiv_categories"] ?? null} />
      <div className="grid gap-6 sm:grid-cols-2">
        <Slider label="Time window" value={windowDays} onValueChange={setWindowDays} min={1} max={90} formatValue={(v) => `${v} day${v === 1 ? "" : "s"}`} />
        <Slider label="Cards per deck" value={deckSize} onValueChange={setDeckSize} min={5} max={50} formatValue={(v) => `${v} papers`} />
      </div>
      <div className="flex justify-end">
        <Button variant="primary" onClick={save} disabled={!dirty} loading={update.isPending}>
          Save sources
        </Button>
      </div>
    </div>
  );
}

export default function ProjectSettingsPage() {
  const projectId = useProjectIdParam();
  const project = useProject(projectId);
  const update = useUpdateProject(projectId);
  const remove = useDeleteProject();
  const navigate = useNavigate();
  const [confirming, setConfirming] = useState(false);
  const [formKey, setFormKey] = useState(0);

  // Re-seed the forms when another tab or the wizard changed the project.
  const updatedAt = project.data?.updated_at;
  useEffect(() => setFormKey((key) => key + 1), [updatedAt]);

  if (projectId === null) return null;
  if (project.isPending) {
    return (
      <section className="mx-auto flex w-full max-w-3xl flex-col gap-4">
        <Skeleton className="h-10 w-64" />
        <Skeleton className="h-64 rounded-card" />
        <Skeleton className="h-64 rounded-card" />
      </section>
    );
  }
  if (project.isError) {
    return <EmptyState title="Project not found" description={project.error.message} />;
  }
  const data = project.data;

  return (
    <section aria-labelledby="page-title" className="mx-auto flex w-full max-w-3xl flex-col gap-6">
      <PageHeader eyebrow={data.name} title="Project settings" />
      {!data.active ? (
        <Card variant="inset" padding="md" className="flex flex-wrap items-center justify-between gap-3">
          <p className="text-[14px] text-text">This project is a draft. Launch it to start daily decks.</p>
          <Button variant="primary" onClick={() => navigate(`${paths.newProject()}?project=${data.id}&step=4`)}>
            Continue setup
          </Button>
        </Card>
      ) : null}
      <Section title="Basics" description="Your question drives the ranking: new papers are scored by how close they are to it, and papers that mention your keywords get a boost.">
        <BasicsForm key={`basics-${formKey}`} project={data} />
      </Section>
      <Section title="Sources" description="Which arXiv listings feed the deck, how far back it looks and how many cards it holds.">
        <SourcesForm key={`sources-${formKey}`} project={data} />
      </Section>
      <Section title="Seed papers" description="Papers you already trust. The deck favours papers similar to them, citing them or cited by them.">
        <SeedAdder projectId={data.id} />
        <SeedList projectId={data.id} />
      </Section>
      {data.active ? (
        <Section title="Daily decks">
          <Switch
            label="Build a new deck every morning"
            description="Turn off to pause this project without deleting anything."
            checked={data.active}
            disabled={update.isPending}
            onCheckedChange={(checked) =>
              update.mutate({ active: checked }, { onError: (error) => toast.error(errorMessage(error, "Could not update the project")) })
            }
          />
        </Section>
      ) : null}
      <Card padding="lg" className="flex flex-col gap-4 border-not-relevant/30">
        <header className="flex flex-col gap-1">
          <h2 className="text-[17px] font-semibold text-not-relevant">Danger zone</h2>
          <p className="text-[13.5px] text-muted">
            Deleting the project removes its decks, ratings, library, seed list and uploaded documents. Shared paper records stay.
          </p>
        </header>
        <div>
          <Button variant="danger" leadingIcon={<Trash2 aria-hidden="true" />} onClick={() => setConfirming(true)}>
            Delete project
          </Button>
        </div>
      </Card>
      <ConfirmDialog
        open={confirming}
        onOpenChange={setConfirming}
        tone="danger"
        title={`Delete “${data.name}”?`}
        description="This cannot be undone. Your ratings, library, seeds and documents for this project are deleted."
        confirmLabel="Delete project"
        pending={remove.isPending}
        onConfirm={() => {
          // Leave first: queries of a deleted project that are still mounted would refetch into 404s.
          const { id, name } = data;
          void navigate(paths.projects());
          remove.mutate(id, {
            onSuccess: () => toast.success(`Deleted ${name}`),
            onError: (error) => toast.error(errorMessage(error, "Could not delete the project")),
          });
        }}
      />
    </section>
  );
}
