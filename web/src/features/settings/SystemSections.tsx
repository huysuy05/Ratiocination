/**
 * Settings beyond the account (UI.md §13): preferences, appearance, privacy, the embedding model and,
 * for admins, the scheduler, connectors and the daily run.
 */
import { CalendarClock, Cpu, Download, Play, ShieldCheck } from "lucide-react";
import { toast } from "sonner";
import { errorMessage } from "../../api/client";
import { browserTimeZone, isJobActive, useDownloadModel, useJob, useRunJob, useUpdateMe, useUpdateSettings } from "../../api/hooks";
import type { AdminSettings, SystemSettings, User } from "../../api/types";
import { ThemeSegmented } from "../../app/shell/ThemeSwitcher";
import { Badge } from "../../components/ui/Badge";
import { Button } from "../../components/ui/Button";
import { ProgressBar } from "../../components/ui/ProgressBar";
import { SegmentedControl } from "../../components/ui/SegmentedControl";
import { Switch } from "../../components/ui/Switch";
import { formatDateTime, formatRelativeDate, formatUsd } from "../../lib/format";
import { useMotionPreference, type MotionPreference } from "../../lib/motion";
import { LICENSE_NAME, SOURCE_URL } from "../../lib/source";
import { SettingsCard } from "./SettingsCard";

/** Download sizes of the supported models (measured 2026-10-13; SPEC §9). */
const MODEL_DOWNLOAD_SIZES: Readonly<Record<string, string>> = {
  "fastembed/BAAI/bge-small-en-v1.5": "~67 MB",
  "fastembed/BAAI/bge-base-en-v1.5": "~220 MB",
};
const HASHING_MODEL = "local/hashing-v1";

const COMMON_ZONES = [
  "UTC",
  "Europe/London",
  "Europe/Berlin",
  "Europe/Paris",
  "America/New_York",
  "America/Chicago",
  "America/Los_Angeles",
  "Asia/Tokyo",
  "Asia/Shanghai",
  "Asia/Singapore",
  "Asia/Ho_Chi_Minh",
  "Australia/Sydney",
];

export function timeZones(): string[] {
  const intl = Intl as unknown as { supportedValuesOf?: (key: string) => string[] };
  try {
    const all = intl.supportedValuesOf?.("timeZone");
    if (all && all.length > 0) return all.includes("UTC") ? all : ["UTC", ...all];
  } catch {
    // older engines: fall through
  }
  return COMMON_ZONES;
}

export function PreferencesSection({ user }: { user: User }) {
  const update = useUpdateMe();
  const zones = timeZones();
  const current = user.preferences.timezone;
  const save = (preferences: Partial<User["preferences"]>) =>
    update.mutate({ preferences }, { onSuccess: () => toast.success("Preferences saved"), onError: (error) => toast.error(errorMessage(error)) });
  return (
    <SettingsCard title="Preferences">
      <label className="flex flex-wrap items-center gap-3 text-[14px]">
        <span className="w-40 text-text">Time zone</span>
        <select
          value={current ?? ""}
          onChange={(event) => save({ timezone: event.target.value || null })}
          className="h-10 min-w-64 rounded-control border border-border bg-surface-2 px-3 text-[13.5px] text-text hover:border-border-strong focus:border-accent"
        >
          <option value="">This browser's ({browserTimeZone()})</option>
          {zones.map((zone) => (
            <option key={zone} value={zone}>
              {zone}
            </option>
          ))}
        </select>
        <span className="text-[12px] text-faint">Used for the dashboard's days and weeks.</span>
      </label>
      <Switch
        label="Load citations automatically"
        description="When you mark a paper relevant or save it, fetch its references and citing papers in the background."
        checked={user.preferences.auto_expand}
        disabled={update.isPending}
        onCheckedChange={(checked) => save({ auto_expand: checked })}
      />
    </SettingsCard>
  );
}

export function AppearanceSection() {
  const { preference, setPreference } = useMotionPreference();
  return (
    <SettingsCard title="Appearance">
      <div className="flex flex-wrap items-center gap-3 text-[14px]">
        <span className="w-40 text-text">Theme</span>
        <ThemeSegmented />
      </div>
      <div className="flex flex-wrap items-center gap-3 text-[14px]">
        <span className="w-40 text-text">Motion</span>
        <SegmentedControl<MotionPreference>
          label="Motion"
          value={preference}
          onValueChange={setPreference}
          options={[
            { value: "system", label: "System" },
            { value: "reduced", label: "Reduced" },
            { value: "full", label: "Full" },
          ]}
        />
      </div>
    </SettingsCard>
  );
}

export function PrivacySection({ settings }: { settings: SystemSettings }) {
  return (
    <SettingsCard title="Privacy">
      <p className="flex items-start gap-2.5 text-[14px] leading-relaxed text-text">
        <ShieldCheck aria-hidden="true" className="mt-0.5 size-5 shrink-0 text-accent" />
        {settings.privacy_disclosure}
      </p>
      <p className="text-[13px] text-muted">
        {settings.ai_summary.configured
          ? `AI summaries are on (${settings.ai_summary.model}). When you ask for one, only the public titles and abstracts of the papers you kept are sent to Anthropic.`
          : "AI summaries are off on this server; nothing is sent to an AI provider."}
      </p>
      <p className="text-[13px] text-muted">
        Ratiocination is open source ({LICENSE_NAME}).{" "}
        <a href={SOURCE_URL} target="_blank" rel="noopener noreferrer" className="text-accent underline underline-offset-2">
          Read the source code
        </a>
      </p>
    </SettingsCard>
  );
}

export function EmbeddingSection({ settings, admin }: { settings: SystemSettings; admin: boolean }) {
  const download = useDownloadModel();
  const jobId = download.data?.job.id ?? settings.embedding.download_job?.id ?? null;
  const job = useJob(jobId, { enabled: jobId !== null });
  const running = isJobActive(job.data ?? settings.embedding.download_job);
  const { embedding } = settings;
  return (
    <SettingsCard title="Similarity model" description="Papers, your question and your draft are embedded on this server to measure closeness.">
      <div className="flex flex-wrap items-center gap-2 text-[14px]">
        <Cpu aria-hidden="true" className="size-4 text-accent" />
        <span className="font-mono text-[13px] text-text">{embedding.active_model}</span>
        {embedding.fallback_active ? (
          <Badge tone="save" size="sm" variant="dashed">
            Fallback
          </Badge>
        ) : (
          <Badge tone="relevant" size="sm">
            Active
          </Badge>
        )}
      </div>
      {embedding.fallback_active ? (
        <p className="text-[13px] text-muted">
          The configured model ({embedding.default_model}) is not downloaded yet, so{" "}
          {embedding.active_model === HASHING_MODEL ? "a simpler built-in model is used and rankings are less accurate" : `the downloaded model ${embedding.active_model} is used until it is`}.
        </p>
      ) : null}
      {admin && !embedding.model_cached ? (
        running ? (
          <ProgressBar value={job.data?.progress ?? null} label="Model download" showValue />
        ) : (
          <div>
            <Button
              leadingIcon={<Download aria-hidden="true" />}
              loading={download.isPending}
              disabled={settings.offline}
              onClick={() => download.mutate(undefined, { onError: (error) => toast.error(errorMessage(error, "Could not start the download")) })}
            >
              Download model ({MODEL_DOWNLOAD_SIZES[embedding.default_model] ?? "public files"} from Hugging Face)
            </Button>
            {settings.offline ? <p className="mt-2 text-[12px] text-faint">This server runs offline.</p> : null}
          </div>
        )
      ) : null}
    </SettingsCard>
  );
}

function Status({ ok, text }: { ok: boolean; text: string }) {
  return <Badge tone={ok ? "relevant" : "faint"} size="sm" variant={ok ? "soft" : "outline"}>{text}</Badge>;
}

export function AdminSection({ admin }: { admin: AdminSettings }) {
  const update = useUpdateSettings();
  const run = useRunJob();
  const runId = run.data?.job.id ?? null;
  const runJob = useJob(runId, { enabled: runId !== null });
  const daily = runJob.data ?? admin.last_daily;
  const { connectors, scheduler } = admin;
  const budget = connectors.openalex.budget;
  return (
    <SettingsCard title="Administration" description={`${admin.users} user${admin.users === 1 ? "" : "s"} on this server.`}>
      <Switch
        label="Daily schedule"
        description={
          scheduler.enabled && scheduler.next_run_at
            ? `Next run ${formatRelativeDate(scheduler.next_run_at)} (${formatDateTime(scheduler.next_run_at)}, cron “${scheduler.cron}” in ${scheduler.timezone})`
            : "Fetch new papers and build every project's deck each morning."
        }
        checked={scheduler.enabled}
        disabled={update.isPending}
        onCheckedChange={(checked) => update.mutate({ scheduler_enabled: checked }, { onError: (error) => toast.error(errorMessage(error)) })}
      />
      <div className="flex flex-wrap items-center gap-3 rounded-control border border-border bg-surface-2/40 px-3.5 py-3">
        <CalendarClock aria-hidden="true" className="size-4 text-accent" />
        <span className="min-w-0 flex-1 text-[13.5px] text-text">
          {daily
            ? `Last daily run: ${daily.status}${daily.finished_at ? ` · ${formatRelativeDate(daily.finished_at)}` : ""}${daily.message ? ` · ${daily.message}` : ""}`
            : "The daily run has not happened yet."}
        </span>
        <Button
          size="sm"
          leadingIcon={<Play aria-hidden="true" />}
          loading={run.isPending || isJobActive(runJob.data)}
          onClick={() => run.mutate({ kind: "daily" }, { onError: (error) => toast.error(errorMessage(error, "Could not start the daily run")) })}
        >
          Run daily now
        </Button>
      </div>
      {isJobActive(runJob.data) ? <ProgressBar value={runJob.data?.progress ?? null} label="Daily run" showValue /> : null}
      <table className="w-full text-[13px]">
        <caption className="sr-only">Connectors</caption>
        <tbody>
          <tr className="border-t border-border">
            <th scope="row" className="py-2 text-left font-medium text-text">OpenAlex</th>
            <td className="py-2 text-muted">
              <Status ok={connectors.openalex.has_key} text={connectors.openalex.has_key ? "API key" : "No key"} /> {formatUsd(budget.spent_today_usd)} of{" "}
              {formatUsd(budget.daily_budget_usd)} today{budget.stopped ? " · stopped for today" : ""}
            </td>
          </tr>
          <tr className="border-t border-border">
            <th scope="row" className="py-2 text-left font-medium text-text">Semantic Scholar</th>
            <td className="py-2 text-muted">
              <Status ok={connectors.semantic_scholar.has_key} text={connectors.semantic_scholar.has_key ? "API key" : "Shared pool"} />
              {connectors.semantic_scholar.breaker_until ? ` paused until ${formatDateTime(connectors.semantic_scholar.breaker_until)}` : ""}
            </td>
          </tr>
          <tr className="border-t border-border">
            <th scope="row" className="py-2 text-left font-medium text-text">arXiv</th>
            <td className="py-2 text-muted">{connectors.arxiv.breaker_until ? `Paused until ${formatDateTime(connectors.arxiv.breaker_until)}` : <Status ok text="Available" />}</td>
          </tr>
          <tr className="border-t border-border">
            <th scope="row" className="py-2 text-left font-medium text-text">Email</th>
            <td className="py-2 text-muted">
              <Status ok={connectors.smtp.configured} text={connectors.smtp.configured ? "SMTP configured" : "Not configured"} />
            </td>
          </tr>
          <tr className="border-t border-border">
            <th scope="row" className="py-2 text-left font-medium text-text">Sign-in</th>
            <td className="flex flex-wrap gap-1.5 py-2 text-muted">
              <Status ok={connectors.oauth.google} text={connectors.oauth.google ? "Google on" : "Google off"} />
              <Status ok={connectors.oauth.github} text={connectors.oauth.github ? "GitHub on" : "GitHub off"} />
            </td>
          </tr>
        </tbody>
      </table>
    </SettingsCard>
  );
}
