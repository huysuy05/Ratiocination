/**
 * REST contract types — an exact mirror of docs/API.md v0.3 (snake_case, discriminated unions).
 * Helper types that are not in API.md are grouped at the end of the file.
 */

// ---------------------------------------------------------------------------------------------
// Errors
// ---------------------------------------------------------------------------------------------

export interface ErrorEnvelope {
  error: { code: ErrorCode; message: string; request_id: string; details?: ErrorDetails };
}

export type ErrorCode =
  | "not_found"
  | "validation_error"
  | "conflict"
  | "unresolvable_identifier"
  | "source_unavailable"
  | "budget_exceeded"
  | "payload_too_large"
  | "unsupported_media_type"
  | "unauthenticated"
  | "unauthorized"
  | "forbidden"
  | "invalid_credentials"
  | "invalid_token"
  | "email_taken"
  | "email_unverified"
  | "password_required"
  | "not_allowed"
  | "rate_limited"
  | "quota_exceeded"
  | "not_configured"
  | "not_enough_papers"
  | "not_implemented"
  | "internal_error";

export type ErrorDetails =
  | { fields: { path: string; message: string }[] } // validation_error; dotted paths; never echoes input values
  | { job: Job } // conflict on rebuild
  | { document_id: number } // conflict on duplicate upload
  | { retry_after_seconds: number } // rate_limited, quota_exceeded
  | Record<string, unknown>;

// ---------------------------------------------------------------------------------------------
// Shared types
// ---------------------------------------------------------------------------------------------

export type ISODateTime = string;
export type ISODate = string;

export type PaperState = "relevant" | "not_relevant" | "saved";
export type FeedbackAction = "relevant" | "not_relevant" | "save" | "open";
export type SlateMode = "daily" | "more";
export type JobStatus = "queued" | "running" | "succeeded" | "failed";
export type JobKind =
  | "daily"
  | "ingest_feeds"
  | "backfill"
  | "enrich"
  | "embed"
  | "build_deck"
  | "expand"
  | "breakthroughs"
  | "process_document"
  | "download_model"
  | "summarize_findings";
export type Relation = "CITES" | "SEMANTICALLY_SIMILAR" | "HAS_TOPIC" | "AUTHORED";
export type FeatureName = "question" | "anchor" | "keyword" | "draft" | "citation" | "preference" | "recency";
export type FeedbackSource = "deck" | "detail" | "library" | "graph" | "dashboard";

export interface Job {
  id: number;
  kind: JobKind;
  status: JobStatus;
  project_id: number | null;
  params: Record<string, unknown>;
  stats: Record<string, unknown>;
  progress: number | null; // 0..1 when known
  message: string | null; // e.g. "Embedding 412 papers"
  error_code: string | null; // source_unavailable | budget_exceeded | egress_blocked | interrupted | internal_error | …
  error_message: string | null;
  created_at: ISODateTime;
  started_at: ISODateTime | null;
  finished_at: ISODateTime | null;
}

export interface Author {
  name: string;
  position: number;
  openalex_id: string | null;
}

export interface TopicRef {
  concept_id: number;
  label: string;
  score: number | null;
} // ≤5, score desc; topics[0] = primary

export interface PaperSummary {
  id: number;
  title: string;
  authors: Author[];
  abstract: string | null;
  publication_date: ISODate | null;
  year: number | null;
  primary_category: string | null;
  categories: string[];
  venue: string | null;
  identifiers: { doi: string | null; arxiv: string | null; openalex: string | null; s2: string | null };
  latest_version: string | null; // "v3" (highest N), else "published", else null
  links: { abs: string | null; pdf: string | null; doi: string | null; oa: string | null };
  topics: TopicRef[];
  status: "active" | "stub" | "retracted" | "withdrawn";
  is_oa: boolean | null;
  cited_by_count: number | null;
  cd: number | null; // stored disruption (CD) index, when computed
  read_at: ISODateTime | null; // the current user's read mark
}

export interface ExpansionStatus {
  status: "none" | "queued" | "running" | "done" | "failed";
  job_id: number | null;
  last_expanded_at: ISODateTime | null;
  message: string | null;
}

// ---- Reasons (discriminated by `code`) ----
export type AnchorKind = "seed" | "relevant" | "saved";
export interface AnchorSimilarityEvidence {
  anchor_paper_id: number;
  anchor_title: string;
  anchor_kind: AnchorKind;
  cosine: number;
}
export interface AnchorEdgeEvidence {
  anchor_paper_id: number;
  anchor_title: string;
  anchor_kind: AnchorKind;
  direction: "cites" | "cited_by";
}
export interface DraftEvidence {
  document_id: number;
  chunk_id: number;
  filename: string;
  section: string;
  page: number | null;
  snippet: string;
}
export interface DeletedDocumentEvidence {
  deleted_document: true;
}
export interface KeywordMatch {
  keyword: string; // the project keyword as the user wrote it
  field: "title" | "abstract";
  text: string; // the paper's own words that matched, e.g. "Conformal Predictions"
}
export interface ReasonBase<C extends string, K extends "metadata" | "model", E> {
  code: C;
  kind: K;
  text: string;
  evidence: E;
  contribution: number | null;
}
export type Reason =
  | ReasonBase<"question", "model", { cosine: number }>
  | ReasonBase<"similar_to_anchor", "model", AnchorSimilarityEvidence>
  | ReasonBase<"draft_match", "model", DraftEvidence | DeletedDocumentEvidence>
  | ReasonBase<"cites_anchor" | "cited_by_anchor", "metadata", AnchorEdgeEvidence>
  | ReasonBase<"preference", "model", { n_labels: number; probability: number }>
  | ReasonBase<"category", "metadata", { category: string }>
  | ReasonBase<"recent", "metadata", { days: number }>
  | ReasonBase<"keyword", "metadata", { matches: KeywordMatch[] }>; // title matches first
// Ordered by the backend; the card shows reasons.slice(0, 3).

export interface FeatureScore {
  value: number | null;
  raw: number | null;
  weight: number;
  contribution: number;
  available: boolean;
}
export type FeatureScores = Record<FeatureName, FeatureScore>; // all 7 keys; score = Σ contributions

export interface RecommendationItem {
  slate_id: number;
  paper: PaperSummary;
  rank: number; // 1-based
  score: number;
  features: FeatureScores;
  reasons: Reason[];
  state: PaperState | null; // current project state; null = not acted on yet
  last_interaction_id: number | null;
}

// ---------------------------------------------------------------------------------------------
// Accounts
// ---------------------------------------------------------------------------------------------

export type AuthMode = "local" | "accounts";
export type OAuthProvider = "google" | "github";

export interface AuthConfig {
  mode: AuthMode;
  providers: { password: boolean; google: boolean; github: boolean };
  allowlist: boolean; // only some emails may sign up
  email_delivery: boolean; // SMTP configured: verification + reset emails are sent
  password_signup: boolean; // email + password sign-up is open (off on a public server without SMTP)
  email_verification: boolean; // new password accounts confirm their email before they get a session
}

export interface UserPreferences {
  auto_expand: boolean;
  timezone: string | null; // IANA zone; null = server default
}

export interface Identity {
  provider: OAuthProvider;
  email: string | null;
  created_at: ISODateTime;
}

export interface User {
  id: number;
  email: string | null;
  email_verified: boolean;
  display_name: string;
  avatar_url: string | null;
  is_admin: boolean;
  has_password: boolean;
  identities: Identity[];
  preferences: UserPreferences;
  created_at: ISODateTime;
}

export interface Me {
  mode: AuthMode;
  user: User;
}

export interface SignUpRequest {
  email: string;
  password: string; // 10–256
  display_name?: string; // 1–100
  next?: string; // in-app path the verification link continues to
}
/** `202` answer of a sign-up that must confirm its email first (no session yet). */
export interface VerificationPending {
  verification_required: true;
  email: string;
}
export interface SignInRequest {
  email: string;
  password: string;
}
export interface ForgotPasswordRequest {
  email: string;
}
export interface ResetPasswordRequest {
  token: string;
  password: string;
}
export interface VerifyEmailRequest {
  token: string;
  password?: string; // required (401 password_required) outside the browser that asked for the link
}
/** Signed out: the address to send a new link to (always 202). Signed in: no body. */
export interface ResendVerificationRequest {
  email?: string;
  next?: string;
}
export interface MeUpdate {
  display_name?: string;
  preferences?: Partial<UserPreferences>;
}
export interface PasswordChangeRequest {
  current_password?: string; // required when has_password
  new_password: string;
}
export interface AccountDeleteRequest {
  confirm_email: string;
}

// ---------------------------------------------------------------------------------------------
// Projects
// ---------------------------------------------------------------------------------------------

export interface ProjectSettings {
  arxiv_categories: string[]; // ≤20, each ^[a-z-]+(\.[A-Za-z-]+)?$ ; default []
  time_window_days: number; // int 1–90; default 14
  slate_size: number; // int 5–50; default 20
}

export interface ProjectStats {
  seeds: number;
  documents: number;
  relevant: number;
  saved: number;
  not_relevant: number;
  remaining_in_deck: number;
  last_deck_at: ISODateTime | null;
}

export interface Project {
  id: number;
  name: string;
  research_question: string;
  description: string;
  keywords: string[]; // ≤ 12, each 2–60 characters; papers that mention them get a boost
  settings: ProjectSettings;
  active: boolean;
  created_at: ISODateTime;
  updated_at: ISODateTime;
  stats: ProjectStats;
}

export interface ProjectCreate {
  name: string; // 1–200
  research_question: string; // 1–4000
  description?: string; // ≤4000
  keywords?: string[]; // ≤12, each 2–60 characters; duplicates (case, hyphen, plural) are dropped
  active?: boolean; // default true
  settings?: Partial<ProjectSettings>;
}

export interface ProjectUpdate {
  name?: string;
  research_question?: string;
  description?: string;
  keywords?: string[]; // replaces the whole list
  active?: boolean;
  settings?: Partial<ProjectSettings>;
}

// ---------------------------------------------------------------------------------------------
// Seeds
// ---------------------------------------------------------------------------------------------

export interface Seed {
  paper: PaperSummary;
  weight: number;
  added_via: "doi" | "arxiv" | "openalex" | "search";
  created_at: ISODateTime;
}
export interface SeedCreate {
  identifier?: string;
  paper_id?: number;
  weight?: number;
} // exactly one of identifier / paper_id; weight 0.1–2 (default 1)
export interface SeedCreated {
  seed: Seed;
  created: boolean;
  enrich_job: Job | null;
} // created=false if already a seed

// ---------------------------------------------------------------------------------------------
// Feed and feedback
// ---------------------------------------------------------------------------------------------

export interface Slate {
  id: number;
  project_id: number;
  generated_at: ISODateTime;
  mode: SlateMode;
  ranker_version: string;
  embedding_model: string;
  pool_size: number;
  items: RecommendationItem[]; // ordered by rank, each with its current state
  remaining: number; // items with state === null
  message: string | null;
}

export type FeedStatus = "needs_setup" | "building" | "ready" | "empty" | "not_built";

export interface FeedResponse {
  status: FeedStatus;
  slate: Slate | null; // latest slate (also while building)
  job: Job | null; // queued/running build_deck job
  last_job: Job | null; // most recent build_deck job of any status
  message: string | null;
}

export interface RebuildRequest {
  mode?: SlateMode;
  prepare?: boolean;
} // defaults "daily", false

export interface FeedbackRequest {
  paper_id: number;
  action: FeedbackAction;
  slate_id?: number | null;
  context?: { source?: FeedbackSource };
}

export interface FeedbackResponse {
  interaction_id: number;
  paper_id: number;
  state: PaperState | null;
  triggered: { expansion_job_id: number | null };
}

export interface UndoRequest {
  interaction_id?: number | null;
}

export interface UndoResponse {
  undone_interaction_id: number;
  paper_id: number;
  state: PaperState | null;
  last_interaction_id: number | null;
}

// ---------------------------------------------------------------------------------------------
// Papers and library
// ---------------------------------------------------------------------------------------------

export type DisruptionLabel = "disruptive" | "mixed" | "consolidating" | "undefined";

export interface DisruptionDetail {
  cd: number | null; // null when undefined (no references or no citations)
  n_citing: number; // N_F
  n_i: number;
  n_j: number;
  n_k: number;
  refs_used: number;
  refs_total: number | null;
  computed_at: ISODateTime;
  label: DisruptionLabel;
  text: string;
}

export interface PaperDetail extends PaperSummary {
  provenance: Record<string, { source: string; observed_at: ISODateTime }>;
  counts: { references: number | null; citations: number | null };
  state: PaperState | null; // null without project_id
  last_interaction_id: number | null;
  recommendation: RecommendationItem | null; // latest slate item for ?project_id
  expansion: ExpansionStatus;
  disruption: DisruptionDetail | null;
}

export interface LibraryItem {
  paper: PaperSummary;
  state: PaperState;
  updated_at: ISODateTime;
  last_interaction_id: number | null;
}

export interface LibraryPage {
  items: LibraryItem[];
  next_cursor: string | null;
  total: number;
}

export interface ReadMark {
  paper_id: number;
  read_at: ISODateTime | null;
}

export interface SearchResults {
  items: PaperSummary[];
}

// ---------------------------------------------------------------------------------------------
// Knowledge graph
// ---------------------------------------------------------------------------------------------

export interface ScholarSummary {
  id: number;
  openalex_id: string;
  display_name: string;
  orcid: string | null;
  works_count: number | null;
  cited_by_count: number | null;
  h_index: number | null;
  institution: string | null;
  analyzed_at: ISODateTime | null;
}

export type NodeType = "work" | "concept" | "scholar" | "author" | "project";

export interface GraphNode {
  id: string; // "work:12" | "concept:5" | "scholar:3" | "author:12:0" | "project:1"
  type: NodeType;
  label: string;
  paper: PaperSummary | null;
  scholar: ScholarSummary | null;
  state: PaperState | null;
  seed: boolean;
  is_center: boolean;
  weight: number | null;
}

export type GraphEdgeEvidence =
  | { relation: "CITES"; contexts: string[]; intents: string[]; is_influential: boolean | null }
  | { relation: "HAS_TOPIC"; score: number | null; subfield: string | null; field: string | null }
  | { relation: "SEMANTICALLY_SIMILAR"; model_id: string; cosine: number }
  | { relation: "AUTHORED"; position: number | null };

export interface GraphEdge {
  id: string; // "e:<db id>" or "a:<paper_id>:<position>"
  source: string; // CITES: citing → cited; AUTHORED: author → work
  target: string;
  relation: Relation;
  verified: boolean;
  confidence: number | null;
  provenance: string; // "s2" | "openalex" | "local/knn" | "paper_authors"
  evidence: GraphEdgeEvidence | null;
}

export type GroupKey =
  | "references"
  | "citations"
  | "similar"
  | "topics"
  | "authors"
  | "author_works"
  | "seeds"
  | "relevant"
  | "saved";

export interface GraphGroup {
  key: GroupKey;
  label: string;
  node_ids: string[];
  count: number;
} // count = total known, node_ids = shown

export interface Neighborhood {
  center: string;
  nodes: GraphNode[];
  edges: GraphEdge[];
  groups: GraphGroup[];
  expansion: ExpansionStatus;
  notices: string[];
}

export interface ProjectGraph {
  nodes: GraphNode[];
  edges: GraphEdge[];
  groups: GraphGroup[];
  notices: string[];
}

export interface ExpandResponse {
  job: Job | null;
  expansion: ExpansionStatus;
  message: string | null;
}

export interface BreakthroughWork {
  paper: PaperSummary;
  cd: number | null;
  n_citing: number | null;
  is_breakthrough: boolean;
}

export interface ScholarBreakthroughs {
  scholar: ScholarSummary;
  position: number;
  works: BreakthroughWork[];
} // works by cd desc, nulls last

export interface Breakthroughs {
  status: "none" | "queued" | "running" | "done" | "failed";
  job: Job | null;
  analyzed_at: ISODateTime | null;
  paper: DisruptionDetail | null;
  scholars: ScholarBreakthroughs[];
  message: string | null;
}

export interface ScholarDetail {
  scholar: ScholarSummary;
  works: BreakthroughWork[];
}

// ---------------------------------------------------------------------------------------------
// Documents (private)
// ---------------------------------------------------------------------------------------------

export interface DocumentOutlineItem {
  heading: string;
  page_start: number | null;
  n_chunks: number;
}

export type DocumentStatus = "uploaded" | "processing" | "ready" | "failed";

export interface DocumentInfo {
  id: number;
  project_id: number;
  filename: string;
  mime_type: string;
  size_bytes: number;
  status: DocumentStatus;
  error: string | null;
  n_sections: number;
  n_chunks: number;
  n_pages: number | null;
  created_at: ISODateTime;
  outline: DocumentOutlineItem[];
  job_id: number | null;
  progress: number | null;
  message: string | null;
}

export interface DocumentAccepted {
  document: DocumentInfo;
  job: Job;
}

export interface DocumentDeleted {
  deleted: {
    file: boolean;
    sections: number;
    chunks: number;
    embeddings: number;
    edges: number;
    scrubbed_items: number;
  };
}

// ---------------------------------------------------------------------------------------------
// Dashboard
// ---------------------------------------------------------------------------------------------

export type DashboardDays = 7 | 30 | 90 | 365;

export interface Period {
  start: ISODate;
  end: ISODate;
  days: number;
  bucket: "day" | "week";
  tz: string;
}

export interface ActivityTotals {
  swiped: number;
  relevant: number;
  not_relevant: number;
  saved: number;
  opened: number;
  read: number;
  decks: number;
}

export interface ActivityPoint {
  date: ISODate; // bucket start
  swiped: number;
  positive: number;
}

export interface DashboardActivity {
  period: Period;
  totals: ActivityTotals;
  previous: ActivityTotals;
  all_time: { swiped: number; read: number; library: number };
  series: ActivityPoint[];
  positive_rate: number | null;
  positive_rate_before: number | null;
  streak_days: number;
  by_project: { project_id: number; name: string; swiped: number; positive: number }[];
}

export interface TopicKey {
  kind: "topic" | "category";
  key: string; // "concept:<id>" | "cat:<code>"
  label: string;
  concept_id: number | null;
  category: string | null;
}

export interface TopicStat extends TopicKey {
  count: number;
  share: number;
  count_before: number;
  share_before: number;
  delta: number;
  weekly: number[]; // 12 values, oldest first, aligned with DashboardTopics.weeks
  swiped: number;
  skip_rate: number | null;
}

export interface DashboardTopics {
  period: Period;
  weeks: ISODate[];
  n_positive: number;
  n_positive_before: number;
  leaning_in: TopicStat[];
  fading: TopicStat[];
  top: TopicStat[];
  skipping: TopicStat[];
  coverage: number | null; // share of liked papers with an OpenAlex topic
}

export interface Finding {
  paper: PaperSummary;
  sentence: string;
  cues: string[];
  score: number;
  similarity: number | null; // to the user's work (SPEC §14.5)
  action: "relevant" | "save";
  acted_at: ISODateTime;
}

export interface FindingGroup {
  topic: TopicKey;
  findings: Finding[];
}

export interface SummaryPoint {
  text: string;
  papers: { id: number; title: string }[];
}

export interface FindingsSummary {
  id: number;
  created_at: ISODateTime;
  model: string;
  n_papers: number;
  themes: { title: string; points: SummaryPoint[] }[];
  dropped_points: number; // points removed because they cited nothing valid
  stale: boolean; // liked papers changed since it was written
}

export interface AiStatus {
  available: boolean;
  model: string | null;
  remaining_today: number;
  job: Job | null;
}

export interface DashboardFindings {
  period: Period;
  n_papers: number;
  n_with_findings: number;
  groups: FindingGroup[];
  summary: FindingsSummary | null;
  ai: AiStatus;
}

export type WorkMatch =
  | { kind: "question" }
  | { kind: "document"; document_id: number; filename: string; section: string; page: number | null; snippet: string };

export interface WorkPaper {
  paper: PaperSummary;
  similarity: number;
  match: WorkMatch;
}

export interface WorkSection {
  document_id: number;
  filename: string;
  section_id: number;
  heading: string;
  n_supporting: number;
  best: WorkPaper | null;
}

export interface WorkQuartile {
  lo: number;
  hi: number;
  n: number;
  positive_rate: number | null;
}

export interface WorkCorrelation {
  r: number;
  n: number;
  strength: "none" | "weak" | "moderate" | "strong";
  text: string;
}

export interface WorkDriftPoint {
  week: ISODate;
  mean_similarity: number | null;
  n: number;
}

export interface WorkProject {
  project_id: number;
  name: string;
  has_documents: boolean;
  threshold: number;
  closest: WorkPaper[];
  quartiles: WorkQuartile[]; // 4 groups, or [] with < 8 swipes
  correlation: WorkCorrelation | null;
  sections: WorkSection[];
  drift: WorkDriftPoint[]; // 12 weeks, oldest first
}

export interface DashboardWork {
  period: Period;
  projects: WorkProject[];
}

export interface SummaryRequest {
  days?: DashboardDays;
  project_id?: number | null;
  tz?: string | null;
}

// ---------------------------------------------------------------------------------------------
// Jobs, settings, health
// ---------------------------------------------------------------------------------------------

export interface RunJobRequest {
  kind: JobKind;
  project_id?: number | null;
  params?: Record<string, unknown>;
}

export interface BudgetInfo {
  provider: string;
  daily_budget_usd: number;
  spent_today_usd: number;
  remaining_today_usd: number | null;
  stop_fraction: number;
  stopped: boolean;
  source: "headers" | "ledger";
}

export interface EmbedderInfo {
  model_id: string;
  dim: number;
  available: boolean;
  reason: string | null;
}

export interface AdminSettings {
  scheduler: { enabled: boolean; next_run_at: ISODateTime | null; cron: string; timezone: string };
  connectors: {
    openalex: { has_key: boolean; budget: BudgetInfo };
    semantic_scholar: { has_key: boolean; breaker_until: ISODateTime | null };
    arxiv: { breaker_until: ISODateTime | null };
    smtp: { configured: boolean };
    oauth: { google: boolean; github: boolean };
  };
  users: number;
  last_daily: Job | null;
}

export interface SystemSettings {
  offline: boolean;
  privacy_disclosure: string;
  embedding: {
    default_model: string;
    active_model: string;
    fallback_active: boolean;
    model_cached: boolean;
    download_job: Job | null;
    available: EmbedderInfo[];
  };
  ai_summary: { configured: boolean; model: string | null };
  admin: AdminSettings | null; // only for admins
  version: string;
}

export interface SettingsUpdate {
  scheduler_enabled?: boolean;
}

export interface Health {
  status: "ok";
  version: string;
  db: "sqlite" | "postgresql";
  offline: boolean;
  embedder: string;
  auth: AuthMode;
  time: ISODateTime;
}

// ---------------------------------------------------------------------------------------------
// Helpers (not in API.md)
// ---------------------------------------------------------------------------------------------

/** `202 { job }` responses. */
export interface JobAccepted {
  job: Job;
}

/** `POST /api/me/sessions/revoke-others`. */
export interface RevokedSessions {
  revoked: number;
}

export type ReasonCode = Reason["code"];
