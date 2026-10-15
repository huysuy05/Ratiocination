# Ratiocination — Implementation Spec v0.3

Source of truth for the build. v0.3 (2026-10-12) replaces v0.2 after the user (a) cut everything a
paper feed does not need — the proposal PDF contained research apparatus and unverifiable detail —
and (b) asked for accounts (Google, GitHub, email + password) and a personal analytics dashboard.
Exact REST shapes: `docs/API.md`. UI: `docs/UI.md`. Build rules: `docs/CONVENTIONS.md`. Decision
log: `docs/DECISIONS.md`. Sections marked *(built)* describe code that exists and is tested.

**Product.** A research-discovery website. Each user signs in and keeps research projects. For each
project the app builds a daily deck of new arXiv papers ranked by fit; the user swipes them (right =
relevant, left = not relevant, up = save); the ranking learns from the swipes. A knowledge graph
links papers, authors and topics; a disruption analysis shows which papers — and which papers of an
author — were breakthroughs. A dashboard shows what the user read and swiped, the main findings of
the papers they liked, how those relate to their own work, and which topics they are leaning into.
Private drafts are processed on the server and never sent to any third party.

## 0. Decisions

- **Website**: React SPA (Vite, TypeScript, Tailwind v4, Motion, TanStack Query, Cytoscape.js).
- **Backend**: Python 3.13, FastAPI, SQLAlchemy 2.x, Alembic, SQLite (WAL) with the transaction model
  of §2.1; code stays PostgreSQL-compatible. One uvicorn worker.
- **Accounts** (§5): email + password, Google (OpenID Connect) and GitHub (OAuth 2). `RADAR_AUTH=local`
  keeps a single-user, sign-in-free mode bound to loopback.
- **Embeddings**: local `fastembed/BAAI/bge-small-en-v1.5` (384-d), queried with its search
  instruction, with the `local/hashing-v1` fallback *(built)*; `fastembed/BAAI/bge-base-en-v1.5` is
  supported as an opt-in (§9). One embedding space; vectors of different models are never compared.
  Kept after a measured comparison on 2026-10-13 (§9): no base-size model was clearly better on the
  users' ratings or on question-style retrieval, and each costs ~3.5× the CPU per paper.
- **Language model**: none in ranking or explanations (templates). One optional use: the dashboard's
  AI summary of findings (§14.4), Claude via the Anthropic Messages API, only when
  `ANTHROPIC_API_KEY` is set, on public titles and abstracts only, labeled "AI-written".
- **Sources**: arXiv RSS (daily), arXiv OAI-PMH (backfill), arXiv API (single lookups, and the
  backfill's fallback when OAI-PMH is throttled, §6), OpenAlex
  (enrichment, citations, disruption counts, authors), Semantic Scholar (citations; optional).
- **Removed in v0.3** (never build): conformal/"high confidence" labels and calibration, the
  evaluation module, SPECTER2 vector fetching, Crossref, candidate generators, exploration slots,
  new-version re-admission, penalties, pin / dismiss-version / hide-topic actions, the topic editor
  and project topics, the costs page, the jobs page, the privacy-mode selector, edge feedback, path
  scores, the versions timeline, the bearer access token.

## 1. Scope

1. Accounts: sign up / sign in (password, Google, GitHub), sign out, password reset and email
   verification (when SMTP is configured), profile, connected accounts, delete account.
2. Projects (per user): name, research question, keywords (≤ 12, §10), description, arXiv categories,
   time window, deck size, active flag.
3. Seeds: add by DOI / arXiv id or URL / OpenAlex id / existing paper id; list; remove. *(built)*
4. Ingestion and enrichment *(built)*: arXiv feeds, OAI-PMH backfill, OpenAlex enrichment. v0.3
   removes the Crossref pass, SPECTER2 fetching and seed-topic copying, and adds OpenAlex author ids.
5. Private documents *(built)*: upload PDF/TXT/MD, local extraction, sections, chunks, embeddings,
   deletion with scrubbing.
6. Ranking: score every recent paper of the project's categories; learned preference; diversity;
   2–3 plain reasons per card.
7. Feedback: relevant, not relevant, save, undo; `open` events; per-user "read" marks.
8. Knowledge graph: works, scholars, topics; citation, similarity, topic and authorship edges;
   neighborhood and project graph; expansion of a paper's citations.
9. Disruption: CD index of a paper; breakthrough analysis of its authors.
10. Library (relevant + saved) with search, read marks and BibTeX export.
11. Dashboard: activity, topics you lean into, main findings (quotes + optional AI summary),
    relation to your work.
12. Settings: personal preferences; admin: daily scheduler, embedding-model download, connector status.

## 2. Module map

```
radar/
  config.py db.py models.py schemas.py errors.py logging_setup.py main.py cli.py keywords.py
  api/        deps, serializers, health, auth, me, projects, seeds, feed, papers, exports, graph,
              documents, dashboard, jobs, settings
  auth/       passwords, sessions, oauth (google, github), accounts, mailer, throttle
  net/        http (HttpClient, breakers), ledger (internal bookkeeping, no UI)
  store/      identifiers, papers, states, reads, graph
  embeddings/ hashing, fastembed backend, registry, vectors, VectorIndex          (built)
  jobs/       runner, registry, scheduler, triggers, pipeline
  connectors/ arxiv_feed, arxiv_api, arxiv_oai, openalex, s2_papers, s2_graph, anthropic
  ingestion/  feeds, backfill, enrich, seeds, vectors                              (built)
  documents/  storage, extract, sections, chunking, service                       (built)
  ranking/    features, preference, mmr, explain, slate
  graph/      knn, expand, neighborhood, project_graph, disruption, breakthroughs
  dashboard/  activity, topics, findings, work, summary
```
Import rule: `radar.documents`, `radar.embeddings`, `radar.ranking` and `radar.dashboard` (except
`dashboard.summary`) never import `radar.net`, `radar.connectors`, `radar.ingestion`, `radar.jobs`,
`radar.graph.expand`, `radar.graph.breakthroughs` or `radar.auth.oauth` (TYPE_CHECKING excepted).

### 2.1 Database transactions *(built)*
Two SQLite engines (read: `BEGIN`; write: `BEGIN IMMEDIATE`), WAL, `busy_timeout` 30 s,
`secure_delete`, foreign keys on. `session_scope(write=...)`; FastAPI deps `get_db`/`get_write_db`
commit before the response is sent. Strict mode in tests (`RADAR_STRICT_TX=1`): reads cannot write;
network calls and `submit_job(session=None)` raise while a write transaction is open. Background
writer (`enqueue_write`/`flush_writes`) for the ledger, job progress and session `last_seen_at`.
Never hold a write transaction across network calls, password hashing or embedding; queue jobs from a
write path with `submit_job(..., session=session)`.

## 3. Configuration

Environment variables (a repo-root `.env` is also read). Secrets are never returned by the API or
logged. *(built)* marks existing settings; the rest are new in v0.3.

| Variable | Default | Meaning |
|---|---|---|
| `RADAR_DATA_DIR`, `RADAR_DATABASE_URL` | `<repo>/data`, `sqlite:///<data>/radar.db` | *(built)* |
| `RADAR_HOST` / `RADAR_PORT` | `127.0.0.1` / `8000` | *(built)* bind |
| `RADAR_TLS_CERT` / `RADAR_TLS_KEY` / `RADAR_BEHIND_TLS_PROXY` | unset | *(built)* TLS here or at a proxy |
| `RADAR_ALLOWED_HOSTS`, `RADAR_DEV_ORIGINS` | unset | *(built)* extra Host / Origin values |
| `RADAR_CLIENT_IP_HEADER` | unset | *(built)* header the TLS proxy sets to the real client IP (Fly.io: `Fly-Client-IP`); replaces the socket peer as the client address (§5.4). Only when every request passes that proxy |
| `RADAR_OFFLINE`, `RADAR_STRICT_TX`, `RADAR_JOBS_INLINE` | `0` | *(built)* |
| `RADAR_EMBEDDER` | `fastembed/BAAI/bge-small-en-v1.5` | *(built)* `fastembed/BAAI/bge-small-en-v1.5`, `fastembed/BAAI/bge-base-en-v1.5` or `local/hashing-v1` (§9) |
| `RADAR_SCHEDULER`, `RADAR_DAILY_CRON`, `RADAR_TIMEZONE` | `0`, `30 0 * * *`, `America/New_York` | *(built)* |
| `OPENALEX_API_KEY`, `RADAR_OPENALEX_DAILY_BUDGET_USD`, `RADAR_BUDGET_STOP_FRACTION` | unset, 1.0/0.1, 0.8 | *(built)* |
| `S2_API_KEY`, `RADAR_S2_BREAKER_MINUTES`, `RADAR_ARXIV_BREAKER_MINUTES` | unset, 15, 10 | *(built)* |
| `RADAR_CONTACT_EMAIL` | unset | *(built)* polite-pool mailto, sent only when set |
| `RADAR_MAX_EXPANSIONS_PER_DAY` | `10` | *(built)* cap on automatic expansions (all users) |
| `RADAR_MAX_UPLOAD_MB` | `50` | *(built)* per file; the upload request body may be 64 KB larger (multipart framing), every other request body at most 64 KB (§16) |
| `RADAR_AUTH` | `accounts` | `accounts` (sign-in required) or `local` (single user, loopback only) |
| `RADAR_PUBLIC_URL` | `http://localhost:<port>` | origin users open; builds OAuth redirect URIs and email links; its origin is allowed by the Origin check; https = `Secure` cookies and HSTS. Must be https on a public server (startup check below). With Vite in development: `http://localhost:5173` |
| `RADAR_PASSWORD_LOGIN` | `1` | email + password accounts on/off |
| `RADAR_GOOGLE_CLIENT_ID` / `RADAR_GOOGLE_CLIENT_SECRET` | unset | Google sign-in (both required) |
| `RADAR_GITHUB_CLIENT_ID` / `RADAR_GITHUB_CLIENT_SECRET` | unset | GitHub sign-in (both required) |
| `RADAR_ALLOWED_EMAILS` | unset | comma list of `name@x.org` or `@x.org`; unset = anyone may sign up |
| `RADAR_ADMIN_EMAILS` | unset | comma list of admin emails (required for non-loopback binds; when unset, the first account is admin) |
| `RADAR_REQUIRE_EMAIL_VERIFICATION` | auto | `1`: a password account gets a session only after it confirms its email (§5.3); `0`: at sign-up. Unset = on for a public server (non-loopback bind, `RADAR_BEHIND_TLS_PROXY=1`, or a `RADAR_PUBLIC_URL` host that is not loopback: `settings.is_public`), off on a loopback-only server |
| `RADAR_SESSION_DAYS` | `30` | session lifetime (sliding) |
| `RADAR_SMTP_HOST` / `_PORT` / `_USERNAME` / `_PASSWORD` / `_FROM` / `_SECURITY` | unset / 587 / – / – / – / `starttls` | outgoing mail for verification and reset links; `_SECURITY` ∈ `starttls`, `ssl`, `none` |
| `ANTHROPIC_API_KEY` | unset | enables the AI summary |
| `RADAR_SUMMARY_MODEL` | `claude-opus-5-5` | Claude model id for the AI summary |
| `RADAR_SUMMARIES_PER_DAY` | `5` | AI summaries per user per UTC day |
| `RADAR_SUMMARIES_GLOBAL_PER_DAY` | `50` | AI summaries per UTC day for all users together (≥ 1; there is no "unlimited"): bounds the daily Anthropic spend however many accounts exist (§14.4) |
| `RADAR_BREAKTHROUGHS_PER_DAY` | `5` | breakthrough analyses per user per UTC day |
| `RADAR_EXPANSIONS_PER_USER_PER_DAY` | `30` | manual expansions per user per UTC day |
| `RADAR_USER_STORAGE_MB` | `500` | total document bytes per user |
| `RADAR_MAX_PROJECTS` | `50` | projects per user |

Removed: `RADAR_ACCESS_TOKEN`, `RADAR_PRIVACY_MODE`.

Startup checks (`main._check_bind`): `RADAR_AUTH=accounts` requires at least one sign-in method
(password login or a complete provider pair). A **public server** (`settings.is_public`: a non-loopback
`RADAR_HOST`, `RADAR_BEHIND_TLS_PROXY=1`, or a `RADAR_PUBLIC_URL` host that is not loopback, e.g. a
loopback bind behind a reverse proxy on the same host) requires `RADAR_AUTH=accounts`,
`RADAR_ADMIN_EMAILS`, TLS (cert + key or `RADAR_BEHIND_TLS_PROXY=1`) and an `https` `RADAR_PUBLIC_URL`:
the `Secure` session cookie and its `__Host-` prefix, HSTS, OAuth redirect URIs and email links all
come from it. A loopback-only server (developer, e2e, local mode) has none of these requirements.
Google and GitHub accept `http` redirect URIs only for `localhost`.

## 4. Data model (`radar/models.py`; Alembic `0001_initial` is regenerated for v0.3)

Conventions *(built)*: integer PK `id`; timestamps are `UTCDateTime`; JSON columns are
`JSON(none_as_null=True)` with callable defaults and treated as immutable values; `0` means
"public/none" in unique keys with owner/project columns; cascading FKs use `ondelete="CASCADE"` with
`passive_deletes=True`.

**Accounts**

| Table | Columns | Keys / notes |
|---|---|---|
| users | id, email?(≤320, lowercase), email_verified, display_name(≤100), avatar_url?, password_hash?, is_admin, created_at, last_login_at?, preferences_json | unique(email). `users.id = 1` always exists (the local user; in accounts mode the first account claims it, §5.6). prefs: `{auto_expand: bool, timezone?: str}` |
| user_identities | id, user_id(cascade), provider('google'\|'github'), subject, email?, created_at, last_login_at | unique(provider, subject); idx(user_id) |
| auth_sessions | id, user_id(cascade), token_hash(sha256 hex), created_at, last_seen_at, expires_at | unique(token_hash); idx(user_id) |
| auth_tokens | id, user_id(cascade), purpose('verify_email'\|'reset_password'), token_hash, email, created_at, expires_at, used_at? | unique(token_hash) |
| oauth_states | id, state_hash, provider, code_verifier, nonce?, next_path, created_at | unique(state_hash); rows older than 10 min are deleted on use |
| app_settings | key(PK, ≤64), value_json, updated_at | global settings: `scheduler_enabled` |

**Projects and papers** (*(built)* unless noted)

| Table | Changes in v0.3 |
|---|---|
| projects | `owner_id → users (cascade)`; settings_json keeps `arxiv_categories`, `time_window_days`, `slate_size`; `keywords_json` (list of ≤ 12 strings, default `[]`; Alembic `0002_project_keywords`) |
| project_seeds, concepts, paper_identifiers, paper_versions, authors, paper_authors | unchanged |
| papers | drop `crossref_checked_at`; add `disruption_cd?` (float), `disruption_ni?`, `disruption_nj?`, `disruption_nk?`, `disruption_refs?` (int), `disruption_at?` |
| paper_authors | add `openalex_author_id?` (`A\d+`) — the OpenAlex author of that position, when known |
| scholars (new) | id, openalex_id(unique), display_name, orcid?, works_count?, cited_by_count?, h_index?, last_known_institution?, fetched_at?, analyzed_at? |
| documents | `owner_id → users`; drop `privacy_mode` |
| document_sections, document_chunks, embeddings | unchanged (`embeddings.entity_type` ∈ paper, chunk, project) |
| graph_edges | `src_type`/`dst_type` ∈ work, concept, scholar, project, document, chunk; relations CITES, SAME_WORK_AS, SEMANTICALLY_SIMILAR, HAS_TOPIC, AUTHORED |
| paper_expansions | unchanged |
| interactions | columns: id, owner_id, project_id(cascade), paper_id, action('relevant'\|'not_relevant'\|'save'\|'open'), timestamp, slate_id?, rank?, context_json, undone_at?; idx(owner_id, timestamp) added |
| project_paper_states | columns: id, project_id(cascade), paper_id, state?('relevant'\|'not_relevant'\|'saved'), updated_at, last_interaction_id? |
| paper_reads (new) | id, user_id(cascade), paper_id(cascade), read_at | unique(user_id, paper_id) |
| recommendation_slates | mode('daily'\|'more'); drop calibration_json |
| recommendation_items | columns: id, slate_id(cascade), paper_id, rank, score, feature_scores_json, explanation_json |
| findings_summaries (new) | id, user_id(cascade), project_id(0 = all), days, paper_ids_json, content_json, model, input_tokens?, output_tokens?, created_at |
| jobs | add `owner_id?` (null = system); kinds in §15 |
| ingestion_watermarks, source_records, duplicate_candidates, cost_ledger | unchanged (internal) |

Dropped: project_topics, edge_feedback, calibration_sets, preference_models.

Stored JSON shapes:
- `recommendation_slates.params_json`: `{slate_size, mmr_lambda, weights: {<feature>: w},
  norm: {<feature>: {lo, hi}}, time_window_days, n_candidates, n_anchors, n_labels, n_keywords,
  preference: {trained: bool, n_pos, n_neg, n_unlabeled}}` (counts only — never keyword text).
- `recommendation_items.feature_scores_json`: `{<feature>: {value, raw, weight, contribution,
  available, evidence}}` for `question, anchor, keyword, draft, citation, preference, recency`;
  evidence is null or: anchor `{paper_id, kind, cosine}`, keyword `{matches: [{keyword, field, text}]}`
  (`[]` when the project has keywords and none matched), draft `{document_id, chunk_id, section,
  page}`, citation `{paper_id, kind, direction}`, preference `{n_labels, probability}` (document
  deletion replaces evidence that names the document with `{deleted_document: true}`). Items stored
  before keywords existed have no `keyword` key; readers report it unavailable with weight 0.
- `recommendation_items.explanation_json`: `Reason[]` exactly as API.md, ordered, text pre-rendered.
- `interactions.context_json`: `{source?: 'deck'|'detail'|'library'|'graph'|'dashboard'}` — never text.
- `graph_edges.evidence_json`: CITES `{contexts, intents, is_influential}`; HAS_TOPIC `{score,
  subfield, field}`; SEMANTICALLY_SIMILAR `{model_id, cosine}`; SAME_WORK_AS `{matched_on}`;
  AUTHORED `{position}`.
- `findings_summaries.content_json`: `{themes: [{title, points: [{text, cites: [paper_id]}]}],
  dropped_points: int}`.

## 5. Accounts and authentication (`radar/auth/`, `radar/api/auth.py`, `radar/api/me.py`)

### 5.1 Modes
- `RADAR_AUTH=local`: every request acts as user 1 (display name "You"); sign-in endpoints answer
  404; the server refuses non-loopback binds. Tests default to this mode.
- `RADAR_AUTH=accounts`: every `/api` route except `/api/health`, `/api/auth/*` and `/api/me`
  (GET, which returns 401 when signed out) requires a session; otherwise 401 `unauthenticated`.

### 5.2 Sessions (`auth/sessions.py`)
- Token: `secrets.token_urlsafe(32)`; the database stores only `sha256(token)` hex.
- Cookie `radar_session` (`__Host-radar_session` when Secure): `HttpOnly; SameSite=Lax; Path=/`,
  `Max-Age` = `RADAR_SESSION_DAYS` days; `Secure` when `RADAR_PUBLIC_URL` is https.
- A request with a valid, unexpired session gets `request.state.user`. `last_seen_at` is written
  through the background writer at most once per 5 minutes per session; when more than a day has
  passed since the last extension, `expires_at` slides to now + `RADAR_SESSION_DAYS` and the cookie
  is re-issued.
- Sign-in always creates a new session (no fixation). Sign-out deletes the session and clears the
  cookie. Password change, password reset and account deletion delete the user's other sessions (all
  sessions for reset/deletion). Expired sessions are deleted by the daily job.

### 5.3 Password accounts (`auth/passwords.py`)
- Argon2id via `argon2-cffi` `PasswordHasher()` (RFC 9106 low-memory profile: t=3, m=64 MiB, p=4);
  re-hash on login when `check_needs_rehash`. Hashing runs outside write transactions.
- Password rules: 10–256 characters; not equal to the email or its local part (case-insensitive).
- Email: trimmed, lowercased, `^[^@\s]+@[^@\s]+\.[^@\s]+$`, ≤ 320 characters; must pass the allow-list.
- Sign-in failure (unknown email, wrong password, account without a password) always returns 401
  `invalid_credentials` "Email or password is incorrect"; unknown emails are hashed against a dummy
  hash so timing does not reveal accounts.
- Sign-up with a taken email → 409 `email_taken` (a deliberate trade-off: sign-up reveals that an
  account exists; sign-in and reset do not).
- Email verification (`settings.email_verification_required`, see `RADAR_REQUIRE_EMAIL_VERIFICATION`;
  on for a public server): sign-up creates the account without a session and answers `202
  VerificationPending`; following the emailed link (`POST /api/auth/email/verify`) signs in. The link
  signs in without the password only in the browser that asked for it (HttpOnly cookie `radar_verify`
  = SHA-256 of the token, `Path=/api/auth/email`); elsewhere it first asks for the password (401
  `password_required`), so a stranger who signed up with someone else's address cannot have that
  person's click verify the stranger's password (the owner uses "forgot password" instead). Sign-in
  with the right password of an unverified account → 403 `email_unverified` (only after the password
  matched, so it reveals nothing to a guesser); sessions of unverified accounts do not count. Without
  SMTP, password sign-up is closed (409 `not_configured`, `AuthConfig.password_signup` false) while
  verified password accounts still sign in. On a loopback-only server sign-up signs in at once.
- The allow-list is checked on every path that ends in a session or a verified email: sign-up,
  password sign-in (after the password matched), verification, reset and every OAuth sign-in.
- Every Argon2 hash or verification (the dummy hash too) holds one of 2 slots of a process-wide
  semaphore (`passwords.HASH_CONCURRENCY`): each needs 64 MiB, and 40 handler threads hashing at once
  would need ~2.5 GiB. Extra callers wait.

### 5.4 Throttling (`auth/throttle.py`, in-memory, one worker)
Sliding 15-minute windows: 5 failed sign-ins per email, 30 sign-in, sign-up or signed-out resend attempts
per client IP, 5 wrong current passwords per user on `POST /api/me/password`, 5 reset/verification
emails per email per hour (counted for unknown addresses too). Exceeding → 429 `rate_limited` with
`Retry-After`.
The client IP is the socket peer (`request.client.host`); behind a TLS proxy run uvicorn with
`--proxy-headers --forwarded-allow-ips <proxy>`, or set `RADAR_CLIENT_IP_HEADER` to a header the proxy
itself sets (Fly.io: `Fly-Client-IP`; `main.ClientIpHeaderMiddleware`, last occurrence, IP values only).

### 5.5 OAuth (`auth/oauth.py`)
- `GET /api/auth/oauth/{provider}/start?next=/path`: 404 when the provider is not configured;
  `next` must start with a single `/` (else `/`). Create `oauth_states` (state = `token_urlsafe(32)`
  stored hashed, PKCE `code_verifier` = `token_urlsafe(64)`, Google `nonce`), set cookie
  `radar_oauth` = state (`HttpOnly; SameSite=Lax; Path=/api/auth/oauth; Max-Age=600`) and 302 to:
  - Google: `https://accounts.google.com/o/oauth2/v2/auth` with `client_id, redirect_uri,
    response_type=code, scope="openid email profile", state, nonce, code_challenge,
    code_challenge_method=S256, prompt=select_account`;
  - GitHub: `https://github.com/login/oauth/authorize` with `client_id, redirect_uri,
    scope="read:user user:email", state, code_challenge, code_challenge_method=S256,
    allow_signup=true`.
  `redirect_uri` = `RADAR_PUBLIC_URL + /api/auth/oauth/{provider}/callback`.
- `GET /api/auth/oauth/{provider}/callback?code&state` (or `?error`): the `state` must equal the
  cookie and match an unexpired row, which is deleted (single use). Then, through `HttpClient`
  (providers `google`, `github`; outside any write transaction):
  - Google: POST `https://oauth2.googleapis.com/token` (form: code, client_id, client_secret,
    redirect_uri, grant_type=authorization_code, code_verifier). The ID token comes straight from
    Google over TLS, so (OpenID Connect Core §3.1.3.7) its claims are checked without a signature
    check: `iss` ∈ {`https://accounts.google.com`, `accounts.google.com`}, `aud` = client id, `exp`
    in the future (60 s leeway), `nonce` matches, `email_verified` true. Identity = `sub`; profile =
    `email`, `name`, `picture`.
  - GitHub: POST `https://github.com/login/oauth/access_token` (`Accept: application/json`; form:
    client_id, client_secret, code, redirect_uri, code_verifier) → `GET https://api.github.com/user`
    and `GET https://api.github.com/user/emails` (`Authorization: Bearer`, `Accept:
    application/vnd.github+json`, `X-GitHub-Api-Version: 2022-11-28`). Identity = numeric `id`;
    email = the primary verified email (none → error `no_verified_email`); profile `name or login`,
    `avatar_url`. The access token is discarded after these calls.
  Success → §5.6, new session, clear `radar_oauth`, 302 to `next`. Any failure → 302 to
  `/signin?error=<code>` with code ∈ `oauth_denied`, `oauth_state`, `oauth_failed`,
  `no_verified_email`, `not_allowed`.

### 5.6 Account resolution (`auth/accounts.py`, one short write transaction)
1. An identity `(provider, subject)` exists → its user.
2. Else a user has the same email: if that user's email is verified, or the account has no password
   → link the identity. If the account has an unverified email and a password → link the identity,
   mark the email verified, **remove the password** and delete its sessions (pre-hijacking defense:
   the password was set by someone who never proved they own the address).
3. Else create a user (allow-list applies; `not_allowed` otherwise).
**Admins and user 1** need a verified email (provider, verification link or reset link), except on a
loopback-only server without forced verification, where a password sign-up gets them at once.
Accounts whose verified email is in `RADAR_ADMIN_EMAILS` are admins, re-evaluated at every sign-in:
when the list is set, an admin whose email is no longer listed loses admin at their next sign-in,
unless no other listed admin account exists yet (logged; the server is never left without an admin).
An empty list revokes nothing. User 1 (which keeps any data created in local mode) is claimed by the
first account with a verified email listed in `RADAR_ADMIN_EMAILS`; when the list is empty, by the
first such account while no admin exists, which becomes admin. An account created unverified claims
user 1 when it verifies (its row moves into user 1, unless it already owns data). A non-loopback bind
requires `RADAR_ADMIN_EMAILS`, so on a public server a stranger who signs up first, even with the
owner's address, never becomes admin or inherits local data.

### 5.7 Email (`auth/mailer.py`)
When SMTP is configured: sign-up sends a verification link (`/verify?token=…&next=…`, 48 h; a new
link replaces the old one; open links stop working once the address is verified another way); "forgot
password" sends a reset link (`/reset?token=…`, 30 min) if the email belongs to an account with a
password — the response is always 202. Tokens are `token_urlsafe(32)`, stored hashed, single use.
Mail is sent from a short-lived daemon thread (`smtplib`, 20 s timeout), never inside a transaction
and never through a `jobs` row (that would store the raw token at rest). Without SMTP: on a
loopback-only server password accounts work with unverified emails; on a public server password
sign-up is closed (Google/GitHub still work). For operators without SMTP the CLI prints one-time
links (30 min, single use): `python -m radar.cli reset-link --email …` for an existing account, and
`python -m radar.cli invite-link --email …`, which first creates a password-less account for the
address when there is none (`accounts.invite`; refused with exit 1 when password login is off or
`RADAR_ALLOWED_EMAILS` does not admit the address, exit 2 for an invalid address). Choosing a password
on the link proves the address, so admin rights and user 1 follow §5.6 — the way to bootstrap the first
admin of a public server before SMTP works.

### 5.8 Profile and account (`/api/me`)
Display name; avatar from the provider; connected identities (link another provider while signed in
via `start?link=1`, which attaches the identity to the current user instead of signing in; unlink
allowed while another sign-in method remains); set or change the password (current password required
when one exists); sign out other sessions; delete the account (type the email to confirm; the last
admin cannot be deleted while other users exist): deletes every project via
`delete_project_private_data`, the user's documents, reads, summaries, sessions, identities,
tokens, interactions and jobs' owner reference, then the user row; checkpoints the WAL.

### 5.9 Authorization
- `current_user` dependency → `CurrentUser(id, is_admin)`. `get_project_or_404(session, project_id,
  user)` returns 404 for other users' projects (never 403, so ids reveal nothing).
- Public data (papers, identifiers, versions, concepts, public edges, scholars, disruption values) is
  shared by all users; everything with a project or owner is private to its owner.
- Jobs: a user sees jobs they own or of their projects; admins see all. Global jobs (ingest_feeds,
  enrich, embed, daily, download_model) can be run by admins only; `backfill` and `build_deck` by the
  project owner.
- Quotas (§3) answer 429 `quota_exceeded` with a human message ("You can run 5 breakthrough analyses
  per day").

## 6. Ingestion and enrichment *(built; v0.3 changes listed)*

Identifier normalization, upsert/dedup/versions, arXiv feed ingestion with per-feed watermarks,
OAI-PMH backfill per OAI set, OpenAlex lookups (free single-work lookups by DOI/arXiv/W id;
placeholder `YYYY-01-01` dates dropped), topics → concepts + HAS_TOPIC edges (top topic always,
others with score ≥ 0.1), CITES edges to known referenced works, retraction status, retry after 2
days and give up after 60 days. Seeds resolve locally first, then arXiv API → OpenAlex.
v0.3 changes: remove the Crossref connector and pass (DOI seeds resolve through OpenAlex), remove
SPECTER2 fetching (`run_fetch_vectors`) and seed-topic copying; the OpenAlex parser keeps
`authorships[].author.id` so `paper_authors.openalex_author_id` is filled on enrichment.

**Backfill sources and fallback** (`radar/ingestion/backfill.py`, since 2026-10-14). The `backfill` job
harvests OAI-PMH per OAI set (`arXivRaw`, 60 s read timeout, one retry — a throttled page hangs, and 90 s
and 120 s both timed out, so waiting longer buys nothing). For the categories of every set that fails it
then queries the **arXiv API** (`cat:<category> AND submittedDate:[since TO until]`, 200 results per
request, at most 5 pages per category) and upserts those records: arXiv throttles bulk OAI harvesting
from cloud hosts (measured from the Fly machine on 2026-10-14: every `ListRecords` request timed out
while the API answered in 3.6 s), and a new project with no papers is useless. Both paths go through
`radar.net.http` (per-host intervals, breakers, budget, ledger, `RADAR_OFFLINE`), so offline mode blocks
both and nothing bypasses the rate limits. `stats.source` is `arxiv_oai`, `arxiv_api` or
`arxiv_oai+arxiv_api`, the fallback is logged as a warning, the final progress message names the API when
it was used, and `stats.api_*` counts the fallback's own requests and records. The job fails
`source_unavailable` only when both sources are unavailable (a partial OAI harvest keeps its papers and
records `api_failed`).

## 7. Outbound HTTP *(built; v0.3 additions)*

All egress goes through `radar.net.http.HttpClient` (per-host intervals, bounded retries, breakers,
OpenAlex budget, ledger rows with the path only). v0.3 adds providers:
- `google` (`accounts.google.com` is never called by the server; `oauth2.googleapis.com`),
- `github` (`github.com` token endpoint, `api.github.com`),
- `anthropic` (`api.anthropic.com`, retry on 429/500/529, timeout 120 s),
a `data=` (form body) argument, a `bearer=` argument (GitHub only) and a `max_retries=` argument that
lowers (never raises) the retry budget of one call — used by the OAI-PMH harvest, which has a cheaper
fallback (§6) and must not spend its read timeout four times over. Ledger rows for these
providers have cost 0; token usage of AI summaries is stored on `findings_summaries`. Request bodies
are never logged; the redaction
filter also covers `client_secret`, `code`, `code_verifier`, `access_token`, `x-api-key` and the
configured client secrets / Anthropic key values. SMTP is the only egress outside `HttpClient`.

## 8. Documents *(built)*

As built: per-document upload directory (0700/0600), validation (413/415/422), PyMuPDF extraction,
heading detection, chunking (220/320/40 words), local embeddings, reconciliation of stuck documents,
deletion with evidence scrubbing and WAL checkpoint. v0.3: owner checks through the project, a
per-user storage quota (`RADAR_USER_STORAGE_MB`, 413 `quota_exceeded`), no privacy-mode field.

## 9. Embeddings *(built)*

As built (SPEC v0.2 §9): `get_embedder`, `ensure_vectors`, `get_vectors`, `VectorIndex`, model
download job; project question vectors use `embed_queries([question + "\n" + description])`.

**Models** (`fastembed_backend.SPECS`, each a `ModelSpec`: fastembed name, dimension, query and
passage instruction, similarity threshold): `fastembed/BAAI/bge-small-en-v1.5` (default; 384-d,
67 MB int8 ONNX) and `fastembed/BAAI/bge-base-en-v1.5` (opt-in via `RADAR_EMBEDDER`; 768-d, 219 MB
int8 ONNX), plus `local/hashing-v1`. Queries (the research question + description) get the bge search
instruction `Represent this sentence for searching relevant passages: `; passages (papers, document
chunks) get none. fastembed 0.9's `query_embed` adds no instruction for these models, so the backend
prefixes the text itself and calls `TextEmbedding.embed` for both sides. The stored `content_hash`
covers exactly what the model saw (instruction + text), so a changed instruction re-embeds the stored
question vectors at the next deck build, and passages (no instruction) keep theirs.

**Batching**: texts are sorted by length and embedded at most 32 per ONNX call and at most 4 096
estimated tokens (characters / 4, each text capped at 512) per call — 32 titles, but 8 full-length
abstracts. Attention memory grows with batch × length² and ONNX Runtime keeps its peak, so this cuts
the peak RSS of embedding 2 000 abstracts with bge-small from ~1.7 GB (32 per call) to ~0.7 GB,
with no loss of speed (measured single-thread, 2026-10-13).

**Choosing and switching models**: vectors are stored per model (`embeddings.model_id`) and every
read filters on the id of the model actually loaded, so vectors of two models are never compared.
When `RADAR_EMBEDDER` names a model that is not downloaded, the first other supported fastembed model
whose files are downloaded is used (e.g. bge-small while bge-base downloads), else `local/hashing-v1`;
Settings shows "Fallback". The download (Settings button, a job in the server process) resets the
registry; `radar download-model` (the CLI, another process) is picked up by the server within 30 s
(`registry.RECHECK_SECONDS`: a model recorded as "not downloaded" is looked for again). After a switch,
each deck build embeds its candidates with the new model (once; up to 2 000 papers per project) and
SEMANTICALLY_SIMILAR edges of the old model are recomputed on their next refresh; old vectors stay in
the table, unused.

**Measured comparison (2026-10-13)** on this machine's data, read-only, each model with its own
instructions (bge/arctic/mxbai: the search instruction on queries; nomic: `search_query: ` /
`search_document: `; granite: none): bge-small, bge-base, arctic-embed-s/m, nomic-embed-text-v1.5
(+ int8), granite-embedding-small-english-r2 and mxbai-embed-large-v1. Signals: the owner's ratings
(2 projects without drafts, 41 rated papers — biased towards bge-small, whose decks produced them),
title → own abstract among 400, citing → cited abstract among 771 (211 pairs), and 13
research-question-style queries → papers of the matching arXiv category (650 papers). The query
instruction mattered (bge-small: separation of relevant from not-relevant cards AUC 0.46 → 0.67 in one
project, title → abstract MRR@10 0.954 → 0.973; arctic-embed-m without its instruction collapses to
0.34). bge-base was within noise of bge-small (category MAP 0.662 vs 0.651, ratings mixed) at ~3.5×
the CPU; arctic-m and nomic were worse on question-style queries (MAP 0.56–0.58); only
mxbai-embed-large (335 M parameters, 1.3 GB) was clearly better (MAP 0.694) at ~10× the CPU, which one
shared CPU cannot afford.

## 10. Ranking (`radar/ranking/`, entry `slate.build_slate(project_id, mode="daily") -> slate_id`)

1. **Candidates**: papers with a title, status `active`, categories intersecting the project's,
   `coalesce(publication_date, first_seen_at.date() for arXiv-ingested papers)` within
   `time_window_days` of today; not a seed of the project; no project state. Excluded: papers shown
   in ≥ 2 earlier slates of the project without any action; mode `more` also excludes papers shown in
   the project's slates of the last 24 h. Newest first, cap 2 000; when the project has keywords, up to
   200 older window papers whose **title** names a keyword are added after the cap (a busy category
   must not push them out of the window).
2. **Vectors**: ensure vectors (default model) for candidates, anchors and the project question.
3. **Anchors**: seeds (weight 1.0), relevant papers (1.0), saved papers (0.8).
4. **Features** (raw value r; normalized v = clip((r − P5)/max(P99 − P5, floor), 0, 1) over the
   candidates, 0.5 when P99 − P5 < 1e-6; floor 0.1 for cosines, 1.0 for the preference logit and 1.0
   for recency, so noise is not stretched to the full range — recency's raw value is already in
   [0, 1], and with floor 0.1 a few days of age inside a two-week window counted as much as the whole
   recency weight, which in a cold-start deck (question + recency only) is 21 % of the score; `citation`
   and `keyword` are on an absolute scale and not normalized):
   - `question` — cosine to the question vector (always available);
   - `anchor` — max over anchors of weight × cosine (available with ≥ 1 anchor);
   - `keyword` — the project's keywords found in the paper (`radar/keywords.py`): each keyword counts
     once, 1.0 when it is in the title, else 0.5 when it is in the abstract; r = Σ, v = 1 − e^(−r)
     (one title match 0.63, two 0.86, three 0.95; one abstract match 0.39). Matching: Unicode NFKC,
     case-folded, hyphens/dashes/slashes/underscores = spaces, tokens = letters and digits (+ a trailing
     `+`/`#`), light plural handling (‑ies → ‑y, ‑es after s/x/z/ch/sh, final ‑s unless ‑ss/‑us/‑is,
     words of ≥ 4 letters), whole words and contiguous phrases only. Available when the project has
     keywords (even if no candidate matches: then every score shrinks by the same factor and the order
     is unchanged);
   - `draft` — max cosine to the project's ready document chunks (available with ≥ 1 chunk);
   - `citation` — 1 when a CITES edge links the paper and an anchor in either direction (available
     when any candidate has such an edge);
   - `preference` — logistic-regression decision value (available when trained);
   - `recency` — 0.5 ** (age_days / 30) (always).
   Score S = Σ wᵢ·vᵢ / Σ wᵢ over available features; contribution cᵢ = wᵢ·vᵢ / Σ w, so S = Σ cᵢ.
   Weights: question 0.30, anchor 0.25, keyword 0.20, draft 0.15, citation 0.07, preference 0.15,
   recency 0.08. A missing feature (no keywords, draft, anchors or ratings) leaves its weight out of
   Σ w, so a project without keywords scores exactly as it would without the keyword feature. In a cold-start deck
   with keywords (question 52 %, keyword 34 %, recency 14 %) one title match lifts a paper by 0.22 —
   as much as 0.42 of the question's normalized range — so a good question match that names a keyword
   passes the best match that does not, while an off-topic paper that only names keywords does not.
5. **Preference head**: scikit-learn `LogisticRegression(C=1.0, max_iter=1000)` on paper vectors with
   `sample_weight`: positives = seeds and relevant (1.0), saved (0.5); negatives = not relevant
   (1.0) plus up to 10 × (number of labels) unlabeled window papers (0.1; `default_rng(project_id)`).
   Trained in every build when there are ≥ 3 positives and ≥ 1 negative (labeled or unlabeled).
6. **Deck**: maximal marginal relevance over candidates: pick argmax of 0.7·S′ − 0.3·max cosine to
   picked papers (S′ = S min-max scaled), `slate_size` cards. A slate is always stored (possibly
   empty with a message: no categories / nothing new in the window).
7. **Reasons** (templates, ordered by contribution, ≤ 3 shown; a `category` or `citation` reason is
   included when one applies):
   | code | text |
   |---|---|
   | question | `Close to your research question` |
   | similar_to_anchor | `Similar to “{title}” ({your seed paper \| which you marked relevant \| which you saved})` |
   | draft_match | `Matches your document “{filename}”, section “{section}”{, p. N}` |
   | cites_anchor / cited_by_anchor | `Cites “{title}” (…)` / `Cited by “{title}” (…)` |
   | preference | `Fits the papers you marked relevant ({n} ratings)` |
   | keyword | `Mentions your keyword ‘{k}’ in the title` · `Mentions your keywords ‘{a}’ in the title and ‘{b}’ in the abstract` (title matches first, ≤ 3 named, then `(+N more)`; metadata reason, emitted whenever a keyword matched; evidence `{matches: [{keyword, field, text}]}` with the paper's own matched words) |
   | category | `Listed in {category}, one of your project's categories` |
   | recent | `Published {n} days ago` |
   Deleting a document rewrites its `draft_match` reasons to `Matched a private document that has
   since been deleted` *(built scrubbing, new text)*.

## 11. Feedback, states, reads (`radar/store/states.py`, `radar/store/reads.py`)

- Actions: `relevant` → state relevant; `not_relevant` → not_relevant; `save` → saved; `open` →
  no state change (recorded for the dashboard; `context.source` says where).
- State = the latest non-undone, non-open interaction of the paper in the project.
- Undo: default target = the latest non-undone, non-open interaction of the project; sets
  `undone_at` and recomputes the state.
- Training labels: state relevant → (1, 1.0); saved → (1, 0.5); not_relevant → (0, 1.0).
- Library = papers whose state is relevant or saved.
- After a relevant or save, the feed router queues an automatic expansion of the paper
  (`jobs.triggers.maybe_queue_expansion`) when the user's `auto_expand` preference is on, the paper
  was not expanded in the last 7 days, has no active expand job, and fewer than
  `RADAR_MAX_EXPANSIONS_PER_DAY` automatic expansions were created today.
- Reads: `paper_reads` per user; `PUT /api/papers/{id}/read {read}` sets or clears `read_at`.
  Independent of project state.

## 12. Knowledge graph (`radar/graph/`, `radar/store/graph.py`)

**Edges** (stored once; readers merge provenances):
- `CITES` work → work; verified; provenance `s2` or `openalex`; evidence contexts/intents when S2.
- `SEMANTICALLY_SIMILAR` work ↔ work (src < dst); unverified; provenance `local/knn`; confidence =
  cosine; for a paper, its top 8 neighbours above the model threshold (`ModelSpec.similarity_threshold`:
  bge-small 0.80, bge-base 0.76 — calibrated to keep the same share of top-8 pairs —, hashing 0.30)
  among papers with vectors; recomputed when older than 7 days or of another model.
- `HAS_TOPIC` work → concept; confidence = OpenAlex topic score. *(built)*
- `AUTHORED` scholar → work; verified; provenance `openalex`; evidence `{position}`.
- `SAME_WORK_AS` (dedup only; never shown).

**Expansion** `run_expand(ctx, paper_id, force=False, auto=False)`: Semantic Scholar references and
citations (≤ 100 each, via `s2_graph`) when reachable, else OpenAlex `referenced_works` (+
`get_works_by_ids`) and `get_citing_works` (≤ 100, newest first); upsert neighbours as `stub` papers
with CITES edges; embed new stubs; compute SEMANTICALLY_SIMILAR edges for the paper; update
`paper_expansions`. Never recursive. Manual `POST /expand` counts toward the user's daily quota and
is skipped (200) when expanded within 7 days unless `force=true`.

**Neighborhood** `GET /api/papers/{id}/neighborhood?project_id=`: the center work; references and
citing works (≤ 25 each; with a project, ordered by question cosine, else by `cited_by_count`);
similar works (≤ 8); topics (≤ 5); authors — scholar nodes when the paper's authors are known as
scholars, else name nodes from `paper_authors` (id `author:<paper_id>:<position>`); when a
breakthrough analysis exists, each scholar's analyzed works (≤ 5 by CD) with AUTHORED edges. Groups:
references, citations, similar, topics, authors, author_works. Notices explain missing data ("Not
expanded yet", "Semantic Scholar unavailable — citations from OpenAlex").

**Project graph** (`GET /api/projects/{id}/graph`): seeds, relevant and saved papers (≤ 150), the
topics at least two of them share (else the 15 most frequent), `CITES`/`SEMANTICALLY_SIMILAR` edges
among them, and the authors on at least two of them (OpenAlex scholar when known, else by name). The
project itself is the page around the graph, not a node.

## 13. Disruption and breakthroughs

**CD index** (`graph/disruption.py`; Funk & Owen-Smith 2017; Wu, Wang & Evans, *Nature* 2019; Park,
Leahey & Funk, *Nature* 2023). For work W with references R = its OpenAlex `referenced_works` (the
first 100; `disruption_refs` = |R| used):
- N_F = count of works citing W (`/works?filter=cites:W&per_page=1&select=id`, read `meta.count`),
- N_B = count citing W and ≥ 1 of R (`filter=cites:W,cites:R1|R2|…`),
- N_R = count citing ≥ 1 of R (`filter=cites:R1|R2|…`),
- n_i = N_F − N_B, n_j = N_B, n_k = N_R − N_B; CD = (n_i − n_j) / (n_i + n_j + n_k) ∈ [−1, 1].
Three OpenAlex list calls ($0.0003). Null when R is empty, N_F = 0, or the denominator is 0. Cached on
the paper for 30 days. Pure function `cd_index(n_f, n_b, n_r) -> CDResult` (unit-tested; inputs are
clamped so n_j ≤ n_f and n_k ≥ 0). Shown with the plain split "of N_F citing papers, n_i cite it
without its references (it eclipses them) and n_j cite both (they build on the same work)", and the
note that counts are all-time (Park et al. use a 5-year window). Live check 2026-10-12, SPECTER
(W3016913119): 54 references, N_F = 468, N_B = 294, N_R = 246 005 → CD ≈ −0.0005.

**Breakthrough analysis** (`graph/breakthroughs.py`, job `breakthroughs`, `run_breakthroughs(ctx,
paper_id)`): OpenAlex lookup of the paper (free) → upsert its authors as scholars (`GET
/authors?filter=openalex:A1|A2|…`, one list call) and AUTHORED edges → CD of the paper → for each of
the first 6 authors by position plus the last author: their 8 most-cited works
(`/works?filter=author.id:A&sort=cited_by_count:desc&per_page=8&select=…,referenced_works`, one list
call) upserted as stub papers with AUTHORED edges → CD of each (skipping values fresher than 30
days). ≈ 150 list calls ≈ $0.015 per paper; budget stops leave partial results and a message.
Results: per scholar, analyzed works ordered by CD (positive = disruptive, near 0 = mixed, negative =
consolidating), with the most disruptive marked "breakthrough" when CD > 0 and N_F ≥ 10.
`GET /api/papers/{id}/breakthroughs` returns the stored results; `POST` queues the job (quota).

## 14. Dashboard (`radar/dashboard/`, `radar/api/dashboard.py`)

All endpoints take `days` ∈ {7, 30, 90, 365} (default 30), optional `project_id` (owned; else all of
the user's projects) and `tz` (IANA; default the user's preference, else `RADAR_TIMEZONE`). Only
non-undone interactions count. Computation is local and read-only (no network).

### 14.1 Activity (`dashboard/activity.py`)
- `totals` for the period and the previous period of equal length: swiped (relevant + not_relevant
  + save actions), relevant, not_relevant, saved, opened (distinct papers with an open event), read
  (papers marked read in the period), decks (slates generated).
- `all_time`: swiped, read, library size.
- `series`: per day (per week when days = 365): swiped, positive (relevant + save).
- `positive_rate` now and before (positives / swiped; null when swiped = 0).
- `streak_days`: consecutive local days ending today (or yesterday when today has none) with ≥ 1 swipe.
- `by_project`: swiped and positives per project.

### 14.2 Topics you lean into (`dashboard/topics.py`)
- Topic of a paper: its HAS_TOPIC edge with the highest confidence → `{kind: "topic", key:
  "concept:<id>", label}`; else its arXiv primary category → `{kind: "category", key: "cat:cs.LG",
  label: "Machine Learning (cs.LG)"}` (static name table of arXiv categories; unknown codes show the code).
- Positive set = papers with a relevant or save action in the period (distinct per paper).
- For each topic: `count`, `share` = count / |positive set|, `count_before`, `share_before` (previous
  period), `delta` = share − share_before, `weekly` = positive counts per week for the last 12 weeks,
  `skip_rate` = not_relevant / swiped for that topic in the period.
- `leaning_in`: count ≥ 2 and delta > 0, ordered by delta then count, ≤ 5. `fading`: count_before ≥ 2
  and delta < 0, ≤ 5. `top`: by count, ≤ 8. `skipping`: ≥ 4 swipes and skip_rate ≥ 0.75, ≤ 5.
  `coverage`: share of positive papers with an OpenAlex topic.

### 14.3 Main findings — quotes (`dashboard/findings.py`)
- Sentence splitter for abstracts (handles e.g., i.e., et al., Fig., decimals, `vs.`).
- Each sentence gets a finding score: result cues (`we show|find|demonstrate|prove|observe|report`,
  `results? (show|indicate|suggest|demonstrate)`, `outperform`, `surpass`, `improv\w* … by`,
  `reduc\w* … by`, `achiev\w*`, `state[- ]of[- ]the[- ]art`, `significant\w*`, `reveal`) +2 each
  (max +4); contribution cues (`we (propose|introduce|present|develop)`) +1; a number or percentage +1;
  position: last two sentences +1, first sentence −1; background cues (`recent(ly)?`, `has attracted`,
  `remains? (challenging|open)`, `in this (paper|work), we (study|consider|investigate)`) −1; length
  outside 8–60 words −2. A paper's finding = its best sentence when score ≥ 2 (verbatim, with the
  matched cue names).
- Response: findings of the positive papers in the period grouped by topic (§14.2), ≤ 4 per topic,
  ≤ 6 topics; within a topic ordered by score, then similarity to the user's work (§14.5), then
  recency of the action. `n_papers`, `n_with_findings`.

### 14.4 Main findings — AI summary (`dashboard/summary.py`, `connectors/anthropic.py`)
- Available when `ANTHROPIC_API_KEY` is set. `POST /api/dashboard/summary {days, project_id?, tz?}` →
  202 job `summarize_findings` (owner = user, `project_id` = the scope project; params `{user_id,
  days, tz?}` so identical requests of different users never share a job, and the job refuses a
  `user_id` other than its owner; a running identical request is returned). Quotas, failed requests
  included: `RADAR_SUMMARIES_PER_DAY` per user and `RADAR_SUMMARIES_GLOBAL_PER_DAY` for all users per
  UTC day (429 `quota_exceeded`, `Retry-After` = seconds to the next UTC midnight). Both are counted
  and the job row inserted in one write transaction (serialized: SQLite `BEGIN IMMEDIATE` plus a
  process lock), so parallel requests cannot pass either cap. `ai.remaining_today` is the smaller of
  the two remainders.
- Input: up to 25 positive papers of the period (most recent action first) as `[k] Title\nAbstract`
  (abstract truncated to 1 500 characters); papers without abstracts are skipped; < 3 papers → 409
  `not_enough_papers`.
- Request: `POST https://api.anthropic.com/v1/messages` (`x-api-key`, `anthropic-version:
  2023-06-01`), model `RADAR_SUMMARY_MODEL`, `max_tokens` 1 500, a tool `report_findings` with a JSON
  schema `{themes: [{title: string ≤ 80, points: [{text: string ≤ 400, cites: int[] ≥ 1}] ≤ 4}] ≤ 6}`
  and `tool_choice: {type: "tool", name: "report_findings"}`. System prompt: summarize what these
  papers found; use only the abstracts given; every point cites the bracket numbers it relies on;
  never add outside facts; say when abstracts disagree.
- Validation: cites outside 1..k are removed; points left without cites are dropped (counted in
  `dropped_points`); bracket numbers in the point text are removed; titles are cut to 80 and points
  to 400 characters, at most 6 themes × 4 points are kept; cites are mapped to paper ids. Stored in
  `findings_summaries`. A reply with no valid point fails the job (`source_unavailable`); a rejected
  key is `not_configured`.
- Only public titles and abstracts are sent — never project text, drafts, names or emails.
- The findings response includes the latest summary for (user, project, days) with `stale` = the
  positive set changed since it was generated.

### 14.5 Relation to your work (`dashboard/work.py`)
For each project in scope, "your work" = the project question vector and its ready document chunks.
Only vectors already stored for the active model are used (deck builds embed the labeled papers, the
question and the chunks); nothing is embedded here and papers without a vector are left out. A
paper's decision in the period is its latest swipe there.
- `s(p)` = max(cos(p, question), max cos(p, chunks)) and the matched part (question or document +
  section + page).
- `closest`: the 5 positive papers (all time) with the highest s, with the matched part.
- `quartiles`: swiped papers of the period split into 4 groups by s; positive rate per group.
- `correlation`: Pearson r between s and the label (1 relevant/saved, 0 not relevant) over swiped
  papers of the period; reported when n ≥ 10 and both labels occur, with strength words (|r| < 0.1
  none, < 0.3 weak, < 0.5 moderate, else strong) and a sentence ("Papers closer to your work were more
  likely to be marked relevant (r = 0.42, n = 120)").
- `sections`: for each ready document section (not References/Bibliography), the number of liked
  papers (all time) whose best chunk match is in it with cosine ≥ the model threshold, and the best
  such paper; sections with none are listed as gaps.
- `drift`: weekly mean cos(positive papers, question) for the last 12 weeks (null weeks allowed).

## 15. Jobs *(runner, registry, scheduler built)*

Registry: `ingest_feeds`, `backfill`, `enrich`, `embed`, `build_deck` (`radar.jobs.pipeline`),
`expand`, `breakthroughs`, `process_document`, `download_model`, `summarize_findings`, `daily`. `jobs.owner_id` is set from the requesting user. `build_deck(mode, prepare=False)`: with
`prepare` it first runs backfill → enrich(limit 100) → embed inline (used right after a project is
created or its categories change). `daily`: ingest_feeds → backfill (projects whose window has < 50
papers) → enrich → embed inline, delete expired sessions/tokens/oauth states, then queue
`build_deck(daily)` per active project. The scheduler flag lives in `app_settings`.

**Startup recovery** (`runner.recover_interrupted`, run from the lifespan before the executor starts).
Every `queued`/`running` job counts as interrupted unless it is proven alive: its `worker_pid` is this
process *and* this process still holds a reference to it, or another process that exists *and* started
no later than the job's `started_at` (`created_at` while queued; Linux reads the process start time from
`/proc/<pid>/stat` field 22 plus `/proc/stat` `btime`). A pid whose start time cannot be read counts as
dead. The direction matters: a job wrongly left `running` holds its `dedupe_key` under `uq_jobs_active`
forever, so every later job of that project answers `conflict` — on 2026-10-14 a container restart reused
a `build_deck` worker's pid and that project could never build a deck again, while the only cost of the
opposite mistake is one job failing `interrupted` and being retried.

## 16. Privacy and security

- Private: project names, questions, keywords, descriptions, documents (bytes, text, chunks, headings,
  filenames), private vectors, interactions, states, reads, summaries. Visible only to the owner;
  never sent to third parties. Lookups of public papers reveal which papers were looked up to the
  providers (seeds, expansions, breakthrough analyses); the AI summary sends public titles and
  abstracts to Anthropic. The Settings page states this.
- Network hardening *(built, adjusted)*: TrustedHost (+ the host of `RADAR_PUBLIC_URL`); non-GET `/api`
  requests need `X-Radar-Client: web` and an allowed `Origin` (CSRF defense for cookie sessions);
  `Referrer-Policy: no-referrer`; request ids. The OAuth callback is a GET protected by state + cookie.
- Request bodies (`main.BodySizeLimitMiddleware`, pure ASGI, before the host/client guards): at most
  64 KB (`MAX_BODY_BYTES`; the largest JSON request, a project with a 4 000-character question and
  description and 12 keywords of 60 characters, is ~9 KB in ASCII and ~36 KB if every character takes
  4 UTF-8 bytes), except `POST
  /api/projects/{id}/documents`: `RADAR_MAX_UPLOAD_MB` + 64 KB of multipart framing (the route still
  refuses a file above `RADAR_MAX_UPLOAD_MB`). A larger `Content-Length` is answered 413
  `payload_too_large` before any byte is read (no `100 Continue`); a body without one (chunked) is
  counted while it streams and answered with the same 413 as soon as it passes the limit. The 413
  carries `Connection: close`. This matters because FastAPI reads the whole body (and spools multipart
  files to disk) before authentication, which is a route dependency. For the same reason an upload without a live session
  (accounts mode) is answered 401 `unauthenticated` with `Connection: close` before any of its body is
  read, so anonymous clients cannot make the server spool `RADAR_MAX_UPLOAD_MB` per request.
- Response headers (`main.security_headers`, every response): `X-Content-Type-Options: nosniff`,
  `X-Frame-Options: DENY`, `Content-Security-Policy` (`frame-ancestors 'none'`; HTML pages get the full
  policy below), `Strict-Transport-Security: max-age=31536000; includeSubDomains` when
  `RADAR_PUBLIC_URL` is https (not for a loopback host), `Cache-Control: no-store` on `/api` unless the
  route set one. SPA policy: `default-src 'self'; script-src 'self' 'sha256-…'; style-src 'self'
  'unsafe-inline'; img-src 'self' data: blob:; media-src 'self'; font-src 'self'; connect-src 'self';
  object-src 'none'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'`. The script hashes
  are computed from the served `web/dist/*.html` itself (read at startup, re-read when the file
  changes), so the pre-paint theme script runs and nothing else inline does; styles need
  `'unsafe-inline'` (libraries insert `<style>` elements and style attributes); OAuth sign-in is a link
  (a navigation), so `form-action` needs no provider. Caching: `/assets/*` (content-hashed names)
  `public, max-age=31536000, immutable`; HTML `no-cache`.
- Logging *(built)*: redaction of keys, emails, secrets, tokens, cookies; no query strings; no request
  bodies. Both database engines use `hide_parameters=True`, so a failing statement never prints its
  bound values (emails, research questions, document text) into an exception or a log line.
- Enforcement tests: import boundary (§2); egress allowlist with canaries (no private text in any
  outbound request, including the Anthropic body); cross-user isolation (user B gets 404 for A's
  projects, documents, jobs, library, dashboard filters); deletion (documents, projects, account) with
  raw-bytes canary checks; secrets never in logs, jobs, ledger or responses.

## 17. Acceptance tests

1. Ingesting the same feed twice creates no duplicates; arXiv + DOI records of one work are one paper. *(built)*
2. Password sign-up, sign-in, sign-out; wrong password and unknown email give the same 401; throttling.
   Public server: no session or admin before email verification; the allow-list holds on every path.
3. OAuth: state/cookie mismatch fails; Google claim checks (iss, aud, exp, nonce, email_verified);
   GitHub picks the primary verified email; account resolution rules incl. the pre-hijacking case.
4. Every private endpoint returns 404 for another user's resources; unauthenticated → 401.
5. Deck items store feature values, contributions and reasons; reasons match stored evidence.
6. relevant / not relevant / save / open / undo set states and training labels as specified.
7. Neighborhood edges carry relation, provenance, verified flag and documented evidence; similarity
   is never shown as a citation.
8. `cd_index` matches hand computations; papers without references get null; breakthroughs are
   idempotent and stop cleanly at the budget.
9. Dashboard: activity totals, streaks, topic shares/deltas, finding extraction on fixed abstracts,
   correlation and quartiles on fixed data.
10. AI summary: request body contains only public titles/abstracts; uncited points are dropped;
    disabled without a key.
11. Deleting a document, a project or an account removes all their data (raw-bytes canary check).
12. BibTeX export lists every library paper once.
13. Every deck action works by click, swipe and keyboard (frontend).
14. Hardening: an oversized `Content-Length` gets 413 before the body is read or authentication runs;
    a chunked body is cut off at its limit; uploads pass up to their own limit; security headers and
    the SPA CSP (with the inline-script hash) are present; the public-server startup matrix holds and
    `fly.toml`'s environment passes it; parallel AI-summary requests cannot pass either cap.

## 18. Deployment

Container image (`Dockerfile`, build context = repo root, `.dockerignore`); runbook for Fly.io: `docs/DEPLOY.md`
(`fly.toml`). The contract any host must meet:

- **Image.** Node stage: `npm ci && npm run build` in `web/`. Python 3.13 runtime: dependencies from
  `backend/requirements.lock.txt` (pinned, no dev tools) in `/opt/venv`; the source tree as in the repo,
  `/app/backend` (`radar`, `migrations`, `alembic.ini`, on `PYTHONPATH`) and `/app/web/dist`, because
  `radar.config` resolves the migrations and the SPA relative to the source tree (a non-editable
  `pip install ./backend` ships neither). Image defaults: `RADAR_DATA_DIR=/data`, `RADAR_HOST=0.0.0.0`,
  `RADAR_PORT=8080`, `RADAR_ENV_FILE=""` (no dotenv).
- **Process.** `python -m radar.cli serve`: uvicorn, ONE worker, the app's redacting log config, the
  startup checks of §3. The entrypoint starts as root only to `chown` the data volume to user `radar`
  (uid 10001), then drops to it. `radar <command>` inside the container runs the CLI as that user;
  `radar backup` writes a consistent SQLite copy to `<data>/backups/`.
- **Environment.** Required on a public host (enforced at startup, §3): `RADAR_AUTH=accounts`,
  `RADAR_ADMIN_EMAILS`, TLS (`RADAR_BEHIND_TLS_PROXY=1` behind the platform's proxy), an https
  `RADAR_PUBLIC_URL`. The first admin can be bootstrapped without SMTP with `radar invite-link --email …`
  (§5.7). Fly.io also sets
  `RADAR_CLIENT_IP_HEADER=Fly-Client-IP` and uvicorn's `FORWARDED_ALLOW_IPS=*` (scheme from
  `X-Forwarded-Proto`). Secrets come from the platform's secret store, never from the image.
- **Volume.** One persistent volume at `RADAR_DATA_DIR`: `radar.db` (+ WAL), `uploads/`, `models/`
  (the embedding model, downloaded only by the explicit admin action), `logs/`. Migrations run at
  startup, so there is no separate release step.
- **Port and health.** HTTP on 8080; TLS ends at the proxy. Health check `GET /api/health` (no session,
  no database access) with a trusted `Host` (`localhost` works on any host name). The first call after a
  start loads the embedding model when it is downloaded (seconds).
- **One machine, always on.** SQLite, the in-process job runner and the daily scheduler mean exactly one
  instance, never scaled to zero. Memory: 2 GB (measured peak ~1.55 GB while embedding a deck).
