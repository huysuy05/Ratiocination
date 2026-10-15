# Ratiocination — REST API contract v0.3

Base path `/api`. JSON with snake_case keys. Backend Pydantic models (`radar/schemas.py`) and frontend
types (`web/src/api/types.ts`) mirror this file exactly. Dates are `YYYY-MM-DD`; datetimes are ISO 8601
UTC with `Z`. IDs are integers unless noted.

## Conventions

- `X-Request-ID` on every response (a client value is echoed only if it matches `^[A-Za-z0-9-]{1,64}$`).
- Every non-GET request must send `X-Radar-Client: web`, and an `Origin` header, if present, must be
  the app's origin (`RADAR_PUBLIC_URL`, own host/port or the Vite dev origin); else 403
  `unauthorized`. Responses carry `Referrer-Policy: no-referrer`, `X-Content-Type-Options: nosniff`,
  `X-Frame-Options: DENY`, a `Content-Security-Policy` (SPEC §16), `Strict-Transport-Security` when
  `RADAR_PUBLIC_URL` is https, and on `/api` `Cache-Control: no-store`.
- Request bodies are limited to 64 KB, except `POST /api/projects/{id}/documents`, which allows
  `RADAR_MAX_UPLOAD_MB` plus 64 KB of multipart framing. A larger body gets 413 `payload_too_large`
  ("Request body too large (at most 64 KB)") with `Connection: close`, before authentication: at once
  for a too large `Content-Length`, as soon as the limit is passed for a chunked body. Send JSON as
  UTF-8.
- Authentication (SPEC §5): in `accounts` mode a session cookie (`radar_session`, HttpOnly) set by
  sign-in; every route except `/api/health`, `/api/auth/*` and `GET /api/me` answers 401
  `unauthenticated` without a valid session. Clients use `fetch(..., { credentials: "same-origin" })`.
  In `local` mode there is no sign-in and every request acts as user 1.
- Resources of other users answer 404 `not_found` (never 403). Admin-only routes answer 403
  `forbidden` to other users.
- Errors use one envelope, including validation errors:

```ts
interface ErrorEnvelope { error: { code: ErrorCode; message: string; request_id: string; details?: ErrorDetails } }
type ErrorCode =
  | "not_found" | "validation_error" | "conflict" | "unresolvable_identifier"
  | "source_unavailable" | "budget_exceeded" | "payload_too_large" | "unsupported_media_type"
  | "unauthenticated" | "unauthorized" | "forbidden" | "invalid_credentials" | "invalid_token"
  | "email_taken" | "email_unverified" | "password_required" | "not_allowed" | "rate_limited" | "quota_exceeded" | "not_configured"
  | "not_enough_papers" | "not_implemented" | "internal_error";
type ErrorDetails =
  | { fields: { path: string; message: string }[] }   // validation_error; dotted paths; never echoes input values
  | { job: Job }                                      // conflict on rebuild
  | { document_id: number }                           // conflict on duplicate upload
  | { retry_after_seconds: number }                   // rate_limited, quota_exceeded (also a Retry-After header)
  | Record<string, unknown>;
```

- Long-running work returns `202 { job: Job }`; poll `GET /api/jobs/{id}` every 1.5 s.
- Cursor pagination: `?limit=&cursor=` → `{ items, next_cursor }` (`null` at the end).

## Shared types

```ts
type ISODateTime = string;
type ISODate = string;

type PaperState = "relevant" | "not_relevant" | "saved";
type FeedbackAction = "relevant" | "not_relevant" | "save" | "open";
type SlateMode = "daily" | "more";
type JobStatus = "queued" | "running" | "succeeded" | "failed";
type JobKind = "daily" | "ingest_feeds" | "backfill" | "enrich" | "embed" | "build_deck" | "expand"
  | "breakthroughs" | "process_document" | "download_model" | "summarize_findings";
type Relation = "CITES" | "SEMANTICALLY_SIMILAR" | "HAS_TOPIC" | "AUTHORED";
type FeatureName = "question" | "anchor" | "keyword" | "draft" | "citation" | "preference" | "recency";
type FeedbackSource = "deck" | "detail" | "library" | "graph" | "dashboard";

interface Job {
  id: number; kind: JobKind; status: JobStatus; project_id: number | null;
  params: Record<string, unknown>; stats: Record<string, unknown>;
  // `backfill` (and `build_deck(prepare)`'s `stats.backfill`) reports its source in `stats.source`:
  // "arxiv_oai" | "arxiv_api" | "arxiv_oai+arxiv_api" ("arxiv_api" = OAI-PMH was unavailable and the
  // arXiv API was used instead, SPEC §6); it fails `source_unavailable` only when both are unavailable.
  progress: number | null;      // 0..1 when known
  message: string | null;       // e.g. "Embedding 412 papers"
  error_code: string | null;    // source_unavailable | budget_exceeded | egress_blocked | interrupted | internal_error | …
                                // `interrupted`: a job of a worker that did not survive (startup recovery, SPEC §15)
  error_message: string | null;
  created_at: ISODateTime; started_at: ISODateTime | null; finished_at: ISODateTime | null;
}

interface Author { name: string; position: number; openalex_id: string | null }
interface TopicRef { concept_id: number; label: string; score: number | null }   // ≤5, score desc; topics[0] = primary

interface PaperSummary {
  id: number; title: string; authors: Author[]; abstract: string | null;
  publication_date: ISODate | null; year: number | null;
  primary_category: string | null; categories: string[]; venue: string | null;
  identifiers: { doi: string | null; arxiv: string | null; openalex: string | null; s2: string | null };
  latest_version: string | null;            // "v3" (highest N), else "published", else null
  links: { abs: string | null; pdf: string | null; doi: string | null; oa: string | null };
  topics: TopicRef[];
  status: "active" | "stub" | "retracted" | "withdrawn";
  is_oa: boolean | null;
  cited_by_count: number | null;
  cd: number | null;                        // stored disruption (CD) index, when computed
  read_at: ISODateTime | null;              // the current user's read mark
}

interface ExpansionStatus {
  status: "none" | "queued" | "running" | "done" | "failed";
  job_id: number | null; last_expanded_at: ISODateTime | null; message: string | null;
}

// ---- Reasons (discriminated by `code`) ----
type AnchorKind = "seed" | "relevant" | "saved";
interface AnchorSimilarityEvidence { anchor_paper_id: number; anchor_title: string; anchor_kind: AnchorKind; cosine: number }
interface AnchorEdgeEvidence { anchor_paper_id: number; anchor_title: string; anchor_kind: AnchorKind; direction: "cites" | "cited_by" }
interface DraftEvidence { document_id: number; chunk_id: number; filename: string; section: string; page: number | null; snippet: string }
interface DeletedDocumentEvidence { deleted_document: true }
interface KeywordMatch { keyword: string; field: "title" | "abstract"; text: string }   // text = the paper's own matched words, e.g. "Conformal Predictions"
interface ReasonBase<C extends string, K extends "metadata" | "model", E> { code: C; kind: K; text: string; evidence: E; contribution: number | null }
type Reason =
  | ReasonBase<"question", "model", { cosine: number }>
  | ReasonBase<"similar_to_anchor", "model", AnchorSimilarityEvidence>
  | ReasonBase<"draft_match", "model", DraftEvidence | DeletedDocumentEvidence>
  | ReasonBase<"cites_anchor" | "cited_by_anchor", "metadata", AnchorEdgeEvidence>
  | ReasonBase<"preference", "model", { n_labels: number; probability: number }>
  | ReasonBase<"category", "metadata", { category: string }>
  | ReasonBase<"recent", "metadata", { days: number }>
  | ReasonBase<"keyword", "metadata", { matches: KeywordMatch[] }>;   // title matches first; each keyword once
// Ordered by the backend; the card shows reasons.slice(0, 3).

interface FeatureScore { value: number | null; raw: number | null; weight: number; contribution: number; available: boolean }
type FeatureScores = Record<FeatureName, FeatureScore>;   // all 7 keys; score = Σ contributions
// Decks built before keywords existed report `keyword` as { available: false, weight: 0, … }.

interface RecommendationItem {
  slate_id: number;
  paper: PaperSummary;
  rank: number;                  // 1-based
  score: number;
  features: FeatureScores;
  reasons: Reason[];
  state: PaperState | null;      // current project state; null = not acted on yet
  last_interaction_id: number | null;
}
```

## Accounts

```ts
type AuthMode = "local" | "accounts";
type OAuthProvider = "google" | "github";
interface AuthConfig {
  mode: AuthMode;
  providers: { password: boolean; google: boolean; github: boolean };
  allowlist: boolean;            // only some emails may sign up
  email_delivery: boolean;       // SMTP configured: verification + reset emails are sent
  password_signup: boolean;      // email + password sign-up is open (false on a public server without SMTP; sign-in still works)
  email_verification: boolean;   // new password accounts confirm their email before they get a session (SPEC §5.3)
}
interface UserPreferences { auto_expand: boolean; timezone: string | null }   // IANA zone; null = server default
interface Identity { provider: OAuthProvider; email: string | null; created_at: ISODateTime }
interface User {
  id: number; email: string | null; email_verified: boolean;
  display_name: string; avatar_url: string | null; is_admin: boolean;
  has_password: boolean; identities: Identity[];
  preferences: UserPreferences; created_at: ISODateTime;
}
interface Me { mode: AuthMode; user: User }
interface SignUpRequest { email: string; password: string; display_name?: string; next?: string }   // password 10–256; display_name 1–100; next: in-app path the verification link continues to
interface VerificationPending { verification_required: true; email: string }   // 202 sign-up answer: no session yet
interface SignInRequest { email: string; password: string }
interface ForgotPasswordRequest { email: string }
interface ResetPasswordRequest { token: string; password: string }
interface VerifyEmailRequest { token: string; password?: string }   // password: needed outside the browser that asked for the link
interface ResendVerificationRequest { email?: string; next?: string }   // signed out: email; signed in: no body
interface MeUpdate { display_name?: string; preferences?: Partial<UserPreferences> }
interface PasswordChangeRequest { current_password?: string; new_password: string }   // current required when has_password
interface AccountDeleteRequest { confirm_email: string }
```

| Method & path | Body | Response |
|---|---|---|
| `GET /api/auth/config` | | `200 AuthConfig` |
| `POST /api/auth/signup` | `SignUpRequest` | `201 Me` + session cookie; while `email_verification` is on: `202 VerificationPending`, no session, a verification link is emailed and an HttpOnly `radar_verify` cookie marks this browser; 409 `email_taken`; 403 `not_allowed`; 409 `not_configured` when `password_signup` is false; 404 when password login is off; 429 `rate_limited` |
| `POST /api/auth/signin` | `SignInRequest` | `200 Me` + session cookie; 401 `invalid_credentials`; only after a correct password: 403 `not_allowed` (allow-list) or 403 `email_unverified` (email not confirmed while `email_verification` is on); 429 `rate_limited` |
| `POST /api/auth/signout` | | `204`, cookie cleared |
| `GET /api/auth/oauth/{provider}/start?next=/&link=0` | | `302` to the provider (404 when not configured). `link=1` (signed in) attaches the identity to the current user |
| `GET /api/auth/oauth/{provider}/callback` | | `302` to `next`, or to `/signin?error=<oauth_denied\|oauth_state\|oauth_failed\|no_verified_email\|not_allowed\|identity_in_use>` |
| `POST /api/auth/password/forgot` | `ForgotPasswordRequest` | `202` always (email sent only when it matches a password account and SMTP is configured) |
| `POST /api/auth/password/reset` | `ResetPasswordRequest` | `200 Me` + session cookie (all other sessions revoked; the email counts as verified); 400 `invalid_token`; 403 `not_allowed` |
| `POST /api/auth/email/verify` | `VerifyEmailRequest` | `200 Me` + session cookie (signs in; works signed in or out); 400 `invalid_token`; 401 `password_required` (details `{ email }`) when `email_verification` is on, the account has a password and the link is opened outside the browser that asked for it; 401 `invalid_credentials` for a wrong `password`; 403 `not_allowed`; 429 `rate_limited` |
| `POST /api/auth/email/resend` | `ResendVerificationRequest` (optional) | Signed out with `{ email }`: `202` whether or not the address has an account waiting (sends only then; sets `radar_verify` either way); 429 `rate_limited` per IP and per address. Signed in, no body: `202`; 409 `conflict` when already verified. Both: 409 `not_configured` without SMTP |
| `GET /api/me` | | `200 Me`; 401 `unauthenticated` |
| `PATCH /api/me` | `MeUpdate` | `200 Me` |
| `POST /api/me/password` | `PasswordChangeRequest` | `200 Me` (other sessions revoked); 401 `invalid_credentials` for a wrong current password; 429 `rate_limited` after 5 wrong current passwords in 15 minutes |
| `DELETE /api/me/identities/{provider}` | | `200 Me`; 409 `conflict` when it is the last sign-in method |
| `POST /api/me/sessions/revoke-others` | | `200 { revoked: number }` |
| `DELETE /api/me` | `AccountDeleteRequest` | `204` (all data deleted, cookie cleared); 422 when the email does not match; 409 for the last admin while other users exist and in local mode |

## Projects

```ts
interface ProjectSettings {
  arxiv_categories: string[];    // ≤20, each ^[a-z-]+(\.[A-Za-z-]+)?$ ; default []
  time_window_days: number;      // int 1–90; default 14
  slate_size: number;            // int 5–50; default 20
}
interface ProjectStats {
  seeds: number; documents: number;
  relevant: number; saved: number; not_relevant: number;
  remaining_in_deck: number; last_deck_at: ISODateTime | null;
}
interface Project {
  id: number; name: string; research_question: string; description: string;
  keywords: string[];            // private like the question; papers that mention them get a boost (SPEC §10)
  settings: ProjectSettings; active: boolean; created_at: ISODateTime; updated_at: ISODateTime;
  stats: ProjectStats;
}
interface ProjectCreate { name: string; research_question: string; description?: string; keywords?: string[]; active?: boolean; settings?: Partial<ProjectSettings> }  // name 1–200, research_question 1–4000, description ≤4000; active default true
interface ProjectUpdate { name?: string; research_question?: string; description?: string; keywords?: string[]; active?: boolean; settings?: Partial<ProjectSettings> }
// keywords: ≤ 12 (else 422 at path "keywords"); each is trimmed with inner whitespace collapsed, then must be
// 2–60 characters with at least one letter or digit (else 422 at "keywords.<i>"); duplicates — case-, hyphen-
// and plural-insensitive ("LLM" = "LLMs", "self-supervised" = "self supervised") — are dropped, the first kept.
// PATCH replaces the whole list ([] clears it; null is 422); like the question, it applies from the next deck.
```

| Method & path | Body | Response |
|---|---|---|
| `GET /api/projects` | | `200 Project[]` (the user's, newest first) |
| `POST /api/projects` | `ProjectCreate` | `201 Project`; 429 `quota_exceeded` beyond `RADAR_MAX_PROJECTS`. When active with categories, queues `build_deck {mode: "daily", prepare: true}` |
| `GET /api/projects/{id}` | | `200 Project` / 404 |
| `PATCH /api/projects/{id}` | `ProjectUpdate` (settings merged key by key, then validated; `keywords` replaced as a whole) | `200 Project`; activating or changing categories/window queues a prepare build |
| `DELETE /api/projects/{id}` | | `204` (deletes all private project data) |

## Seeds

```ts
interface Seed { paper: PaperSummary; weight: number; added_via: "doi" | "arxiv" | "openalex" | "search"; created_at: ISODateTime }
interface SeedCreate { identifier?: string; paper_id?: number; weight?: number }   // exactly one of identifier / paper_id; weight 0.1–2 (default 1)
interface SeedCreated { seed: Seed; created: boolean; enrich_job: Job | null }     // created=false if already a seed
```

| Method & path | Body | Response |
|---|---|---|
| `GET /api/projects/{id}/seeds` | | `200 Seed[]` |
| `POST /api/projects/{id}/seeds` | `SeedCreate` | `201 SeedCreated`; 422 `unresolvable_identifier`; 503 `source_unavailable`. Known papers resolve without network; otherwise arXiv API → OpenAlex. Queues `enrich` (returned) and `embed` |
| `DELETE /api/projects/{id}/seeds/{paper_id}` | | `204` |

## Feed and feedback

```ts
interface Slate {
  id: number; project_id: number; generated_at: ISODateTime; mode: SlateMode;
  ranker_version: string; embedding_model: string; pool_size: number;
  items: RecommendationItem[];     // ordered by rank, each with its current state
  remaining: number;               // items with state === null
  message: string | null;          // e.g. "No new papers in your categories in the last 14 days"
}
interface FeedResponse {
  status: "needs_setup" | "building" | "ready" | "empty" | "not_built";
  slate: Slate | null;             // latest slate (also while building)
  job: Job | null;                 // queued/running build_deck job
  last_job: Job | null;            // most recent build_deck job of any status
  message: string | null;
}
interface RebuildRequest { mode?: SlateMode; prepare?: boolean }   // defaults "daily", false
interface FeedbackRequest { paper_id: number; action: FeedbackAction; slate_id?: number | null; context?: { source?: FeedbackSource } }
interface FeedbackResponse {
  interaction_id: number; paper_id: number; state: PaperState | null;
  triggered: { expansion_job_id: number | null };
}
interface UndoRequest { interaction_id?: number | null }   // default: latest non-undone, non-open interaction of the project
interface UndoResponse { undone_interaction_id: number; paper_id: number; state: PaperState | null; last_interaction_id: number | null }
```

Feed status, in order: `needs_setup` (no arXiv categories) → `building` (a build_deck job is
queued/running; `slate` = previous slate or null) → `ready` (latest slate has ≥1 item) → `empty`
(latest slate has 0 items) → `not_built`. When `last_job.status == "failed"` the UI shows its
`error_message` with Retry.

| Method & path | Body | Response |
|---|---|---|
| `GET /api/projects/{id}/feed` | | `200 FeedResponse` |
| `POST /api/projects/{id}/feed/rebuild` | `RebuildRequest` | `202 { job }`; one active build per project: same mode → the existing job; different mode → `409 conflict` with `details.job` |
| `POST /api/projects/{id}/feedback` | `FeedbackRequest` | `200 FeedbackResponse`; 404 unknown paper; 422 unknown action. `open` returns the unchanged state |
| `POST /api/projects/{id}/feedback/undo` | `UndoRequest` | `200 UndoResponse`; 404 when nothing to undo |

## Papers and library

```ts
interface DisruptionDetail {
  cd: number | null;              // null when undefined (no references or no citations)
  n_citing: number;               // N_F
  n_i: number; n_j: number; n_k: number;
  refs_used: number; refs_total: number | null;   // references used (≤100) / known total
  computed_at: ISODateTime;
  label: "disruptive" | "mixed" | "consolidating" | "undefined";   // disruptive: cd > 0 and ≥55% of citers cite it without its references; consolidating: cd < 0 and ≤45%; else mixed; undefined: cd null
  text: string;                   // plain-language split, e.g. "Of 468 citing papers, 174 cite it without its references…"
}
interface PaperDetail extends PaperSummary {
  provenance: Record<string, { source: string; observed_at: ISODateTime }>;
  counts: { references: number | null; citations: number | null };   // CITES edges known locally
  state: PaperState | null;                    // null without project_id
  last_interaction_id: number | null;
  recommendation: RecommendationItem | null;   // latest slate item for ?project_id
  expansion: ExpansionStatus;
  disruption: DisruptionDetail | null;
}
interface LibraryItem { paper: PaperSummary; state: PaperState; updated_at: ISODateTime; last_interaction_id: number | null }
interface LibraryPage { items: LibraryItem[]; next_cursor: string | null; total: number }
interface ReadMark { paper_id: number; read_at: ISODateTime | null }
```

| Method & path | Body | Response |
|---|---|---|
| `GET /api/papers/{id}?project_id=` | | `200 PaperDetail` / 404 |
| `GET /api/papers?project_id=&states=relevant,saved&read=any\|read\|unread&query=&limit=50&cursor=` | | `200 LibraryPage`: the project's library (`project_id` required; `states` ⊆ {relevant, saved}, default both; `query` filters title/abstract, case-insensitive); order `updated_at DESC, id DESC` |
| `GET /api/search?query=&limit=20` | | `200 { items: PaperSummary[] }` (local title/abstract search) |
| `PUT /api/papers/{id}/read` | `{ read: boolean }` | `200 ReadMark` |
| `POST /api/papers/{id}/disruption?force=false` | | `200 DisruptionDetail` (computed synchronously from OpenAlex, cached 30 days); 422 when the paper has no OpenAlex record; 503/429 `source_unavailable`/`budget_exceeded` |
| `GET /api/exports/bibtex?project_id=&states=relevant,saved` | | `200 text/x-bibtex`, `Content-Disposition: attachment; filename="ratiocination-<id>.bib"` |

## Knowledge graph

```ts
interface ScholarSummary {
  id: number; openalex_id: string; display_name: string; orcid: string | null;
  works_count: number | null; cited_by_count: number | null; h_index: number | null;
  institution: string | null; analyzed_at: ISODateTime | null;
}
type NodeType = "work" | "concept" | "scholar" | "author" | "project";
interface GraphNode {
  id: string;                      // "work:12" | "concept:5" | "scholar:3" | "author:12:0" | "project:1"
  type: NodeType;
  label: string;
  paper: PaperSummary | null;      // works
  scholar: ScholarSummary | null;  // scholars
  state: PaperState | null;        // works, with a project
  seed: boolean;                   // works that are project seeds
  is_center: boolean;
  weight: number | null;           // works: relevance to the project or citation count (for sizing)
}
type GraphEdgeEvidence =
  | { relation: "CITES"; contexts: string[]; intents: string[]; is_influential: boolean | null }
  | { relation: "HAS_TOPIC"; score: number | null; subfield: string | null; field: string | null }
  | { relation: "SEMANTICALLY_SIMILAR"; model_id: string; cosine: number }
  | { relation: "AUTHORED"; position: number | null };
interface GraphEdge {
  id: string;                      // "e:<db id>" or "a:<paper_id>:<position>" for name-only author edges
  source: string; target: string;  // CITES: citing → cited; AUTHORED: author → work
  relation: Relation;
  verified: boolean;
  confidence: number | null;       // SEMANTICALLY_SIMILAR: cosine; HAS_TOPIC: topic score
  provenance: string;              // "s2" | "openalex" | "local/knn" | "paper_authors"
  evidence: GraphEdgeEvidence | null;
}
type GroupKey = "references" | "citations" | "similar" | "topics" | "authors" | "author_works" | "seeds" | "relevant" | "saved";
interface GraphGroup { key: GroupKey; label: string; node_ids: string[]; count: number }   // count = total known, node_ids = shown
interface Neighborhood { center: string; nodes: GraphNode[]; edges: GraphEdge[]; groups: GraphGroup[]; expansion: ExpansionStatus; notices: string[] }
interface ProjectGraph { nodes: GraphNode[]; edges: GraphEdge[]; groups: GraphGroup[]; notices: string[] }
interface ExpandResponse { job: Job | null; expansion: ExpansionStatus; message: string | null }
interface BreakthroughWork { paper: PaperSummary; cd: number | null; n_citing: number | null; is_breakthrough: boolean }
interface ScholarBreakthroughs { scholar: ScholarSummary; position: number; works: BreakthroughWork[] }   // works by cd desc, nulls last
interface Breakthroughs {
  status: "none" | "queued" | "running" | "done" | "failed";
  job: Job | null; analyzed_at: ISODateTime | null;
  paper: DisruptionDetail | null;
  scholars: ScholarBreakthroughs[];
  message: string | null;          // e.g. "Stopped early: OpenAlex daily budget reached (3 of 7 authors analyzed)"
}
```

| Method & path | Body | Response |
|---|---|---|
| `GET /api/papers/{id}/neighborhood?project_id=` | | `200 Neighborhood` |
| `POST /api/papers/{id}/expand?project_id=&force=false` | | `202 ExpandResponse` with a job, or `200` with `job: null` when expanded within 7 days and not forced; 429 `quota_exceeded` |
| `GET /api/projects/{id}/graph?limit=150` | | `200 ProjectGraph` |
| `GET /api/papers/{id}/breakthroughs` | | `200 Breakthroughs` |
| `POST /api/papers/{id}/breakthroughs?force=false` | | `202 { job }`; 429 `quota_exceeded`; 409 `conflict` when the OpenAlex budget is already exhausted today |
| `GET /api/scholars/{id}` | | `200 { scholar: ScholarSummary; works: BreakthroughWork[] }` |

## Documents (private)

```ts
interface DocumentOutlineItem { heading: string; page_start: number | null; n_chunks: number }
interface DocumentInfo {
  id: number; project_id: number; filename: string; mime_type: string; size_bytes: number;
  status: "uploaded" | "processing" | "ready" | "failed"; error: string | null;
  n_sections: number; n_chunks: number; n_pages: number | null;
  created_at: ISODateTime; outline: DocumentOutlineItem[];
  job_id: number | null; progress: number | null; message: string | null;
}
interface DocumentAccepted { document: DocumentInfo; job: Job }
interface DocumentDeleted { deleted: { file: boolean; sections: number; chunks: number; embeddings: number; edges: number; scrubbed_items: number } }
```

| Method & path | Body | Response |
|---|---|---|
| `GET /api/projects/{id}/documents` | | `200 DocumentInfo[]` |
| `POST /api/projects/{id}/documents` | multipart field `file` | `202 DocumentAccepted`; 409 `conflict` (`details.document_id`); 413 `payload_too_large` (file above `RADAR_MAX_UPLOAD_MB`, or a request body above that + 64 KB, refused before it is read) or `quota_exceeded`; 415 |
| `GET /api/documents/{id}/status` | | `200 DocumentInfo` (poll every 1.5 s while `uploaded`/`processing`) |
| `DELETE /api/documents/{id}` | | `200 DocumentDeleted`; 409 `conflict` while `uploaded`/`processing` |

## Dashboard

Query parameters for every GET: `days` ∈ {7, 30, 90, 365} (default 30; anything else is 422),
`project_id` (optional; must be the user's, else 404), `tz` (IANA; default the user's preference,
else the server zone; an unknown zone is 422). The GET views are computed locally and never call out.

```ts
interface Period { start: ISODate; end: ISODate; days: number; bucket: "day" | "week"; tz: string }
interface ActivityTotals { swiped: number; relevant: number; not_relevant: number; saved: number; opened: number; read: number; decks: number }
interface ActivityPoint { date: ISODate; swiped: number; positive: number }   // date = bucket start
interface DashboardActivity {
  period: Period;
  totals: ActivityTotals; previous: ActivityTotals;
  all_time: { swiped: number; read: number; library: number };
  series: ActivityPoint[];
  positive_rate: number | null; positive_rate_before: number | null;
  streak_days: number;
  by_project: { project_id: number; name: string; swiped: number; positive: number }[];
}

interface TopicKey { kind: "topic" | "category"; key: string; label: string; concept_id: number | null; category: string | null }
interface TopicStat extends TopicKey {
  count: number; share: number; count_before: number; share_before: number; delta: number;
  weekly: number[];                // 12 values, oldest first, aligned with DashboardTopics.weeks
  swiped: number; skip_rate: number | null;
}
interface DashboardTopics {
  period: Period; weeks: ISODate[];
  n_positive: number; n_positive_before: number;
  leaning_in: TopicStat[]; fading: TopicStat[]; top: TopicStat[]; skipping: TopicStat[];
  coverage: number | null;         // share of liked papers with an OpenAlex topic
}

interface Finding {
  paper: PaperSummary; sentence: string; cues: string[]; score: number;
  similarity: number | null;       // to the user's work (SPEC §14.5)
  action: "relevant" | "save"; acted_at: ISODateTime;
}
interface FindingGroup { topic: TopicKey; findings: Finding[] }
interface SummaryPoint { text: string; papers: { id: number; title: string }[] }
interface FindingsSummary {
  id: number; created_at: ISODateTime; model: string; n_papers: number;
  themes: { title: string; points: SummaryPoint[] }[];
  dropped_points: number;          // points removed because they cited nothing valid
  stale: boolean;                  // liked papers changed since it was written
}
interface DashboardFindings {
  period: Period; n_papers: number; n_with_findings: number;
  groups: FindingGroup[];
  summary: FindingsSummary | null;
  ai: { available: boolean; model: string | null; remaining_today: number; job: Job | null };
}

type WorkMatch =
  | { kind: "question" }
  | { kind: "document"; document_id: number; filename: string; section: string; page: number | null; snippet: string };
interface WorkPaper { paper: PaperSummary; similarity: number; match: WorkMatch }
interface WorkSection { document_id: number; filename: string; section_id: number; heading: string; n_supporting: number; best: WorkPaper | null }
interface WorkProject {
  project_id: number; name: string; has_documents: boolean; threshold: number;
  closest: WorkPaper[];
  quartiles: { lo: number; hi: number; n: number; positive_rate: number | null }[];   // 4 groups, or [] with < 8 swipes
  correlation: { r: number; n: number; strength: "none" | "weak" | "moderate" | "strong"; text: string } | null;
  sections: WorkSection[];
  drift: { week: ISODate; mean_similarity: number | null; n: number }[];   // 12 weeks, oldest first
}
interface DashboardWork { period: Period; projects: WorkProject[] }
interface SummaryRequest { days?: 7 | 30 | 90 | 365; project_id?: number | null; tz?: string | null }
```

| Method & path | Body | Response |
|---|---|---|
| `GET /api/dashboard/activity` | | `200 DashboardActivity` |
| `GET /api/dashboard/topics` | | `200 DashboardTopics` |
| `GET /api/dashboard/findings` | | `200 DashboardFindings` |
| `GET /api/dashboard/work` | | `200 DashboardWork` |
| `POST /api/dashboard/summary` | `SummaryRequest` | `202 { job }` (`summarize_findings`, params `{user_id, days, tz?}`; a running identical request is returned instead); 409 `not_configured` (no API key) or `not_enough_papers` (< 3 liked papers with abstracts); 429 `quota_exceeded` with `details.retry_after_seconds` (+ `Retry-After`, until the next UTC midnight) when the user made `RADAR_SUMMARIES_PER_DAY` requests today or all users together `RADAR_SUMMARIES_GLOBAL_PER_DAY` (failed ones included; checked atomically with the job insert). `DashboardFindings.ai.remaining_today` is the smaller of the two remainders |

## Jobs, settings, health

```ts
interface RunJobRequest { kind: JobKind; project_id?: number | null; params?: Record<string, unknown> }
interface BudgetInfo { provider: string; daily_budget_usd: number; spent_today_usd: number; remaining_today_usd: number | null; stop_fraction: number; stopped: boolean; source: "headers" | "ledger" }
interface EmbedderInfo { model_id: string; dim: number; available: boolean; reason: string | null }
interface SystemSettings {
  offline: boolean;
  privacy_disclosure: string;      // SPEC §16 text
  embedding: { default_model: string; active_model: string; fallback_active: boolean; model_cached: boolean; download_job: Job | null; available: EmbedderInfo[] };
  // default_model = RADAR_EMBEDDER; fallback_active: it is not downloaded (or failed to load) and active_model is another
  // downloaded fastembed model, else local/hashing-v1 (SPEC §9); available lists every supported model, default first.
  ai_summary: { configured: boolean; model: string | null };
  admin: null | {                  // only for admins
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
  };
  version: string;
}
interface SettingsUpdate { scheduler_enabled?: boolean }   // admin
interface Health { status: "ok"; version: string; db: "sqlite" | "postgresql"; offline: boolean; embedder: string; auth: AuthMode; time: ISODateTime }
```

| Method & path | Body | Response |
|---|---|---|
| `GET /api/jobs?limit=20&kind=&project_id=` | | `200 Job[]` (own jobs; admins: all), newest first |
| `GET /api/jobs/{id}` | | `200 Job` (own or admin) |
| `POST /api/jobs/run` | `RunJobRequest` | `202 { job }`; 403 `forbidden` for global kinds unless admin |
| `GET /api/settings` | | `200 SystemSettings` |
| `PATCH /api/settings` | `SettingsUpdate` | `200 SystemSettings` (admin) |
| `POST /api/settings/embedding/download` | | `202 { job }` (admin; 409 `conflict` when offline) |
| `GET /api/health` | | `200 Health` |

Non-API routes: the backend serves `web/dist` at `/` with SPA fallback when the build exists (HTML
`Cache-Control: no-cache` with a CSP that allows its inline scripts by hash; `/assets/*` `public,
max-age=31536000, immutable`). In
development, Vite (port 5173) proxies `/api` to `http://127.0.0.1:8000`; set
`RADAR_PUBLIC_URL=http://localhost:5173` so OAuth redirects and cookies use the Vite origin.
