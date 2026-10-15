# Ratiocination — UI and interaction design v0.3

A React website with beautiful, purposeful effects: a calm, premium research tool — "Linear meets a
radar scope". Motion explains state changes and is never noisy. Data shapes come from `docs/API.md`.

v0.3 changes: accounts (sign-in pages, user menu, auth gate), the per-user dashboard, disruption and
breakthroughs on papers, scholars in the graph, a read mark instead of a read state. Removed: pin,
version dismissal, topic hiding, calibration/confidence, the costs and jobs pages, the access-token
dialog and the in-browser mock backend (tests stub `fetch` per test; screenshots use the real backend
with a demo database).

## 1. Stack

React 19 + TypeScript (strict) + Vite + Tailwind CSS v4 + Motion (`motion/react`) + TanStack Query +
React Router (data router) + Cytoscape.js with `cytoscape-fcose` + lucide-react + sonner + `radix-ui`
primitives. Fonts are self-hosted (`@fontsource-variable/inter`, `@fontsource-variable/source-serif-4`,
and `@fontsource-variable/outfit` for the landing page only; no third-party font requests). `index.html` sets `<meta name="referrer" content="no-referrer">`.
Charts are hand-written SVG components animated with Motion (no chart library).

## 2. Visual language

Motif: a radar scope — concentric rings, a soft rotating sweep, glowing "blips" — in loading states,
empty states, the auth backdrop and the end-of-deck moment; subtle elsewhere. The radar is not the logo.

Logo (2026-10-13): the R mark from `brand/logo` — a stroke R with a diagonal leg beside a separate right
triangle — as inline SVG in `components/ui/BrandMark.tsx`, traced from the brand PNGs: `BrandMark`
(square box, `size` px) and `BrandLockup` (mark + the "ratiocination" wordmark, `height` px, ≈ 6.26:1).
The R and the wordmark take the current text colour, the triangle `--accent`: Smoky Black + Olive Drab
in light, Floral White + Bone in dark, like the landing page's recoloured lockup (§16). Inside a link
the SVG is decorative and the link is named ("Ratiocination home"); alone it takes a `title`. Favicon
(`web/public`): `favicon.svg` (Floral White R + Bone triangle on a #11120D rounded square, so it reads
on light and dark tab bars), `favicon-32.png` (fallback) and `apple-touch-icon.png` (180 px, full
bleed).

Theme tokens (`styles/tokens.css`): light values on `:root`, dark on `[data-theme="dark"]`; `body`
always has an explicit background. Palette (2026-10-13, user choice): four warm neutrals — Smoky
Black #11120D, Olive Drab #565449, Bone #D8CFBC, Floral White #FFFBF4. Light: Floral White page, warm
white and bone-tinted surfaces, Smoky Black text, Olive Drab muted text and accent (primary buttons
olive with Floral White text). Dark: Smoky Black page, olive-tinted charcoal surfaces, Floral White
text, Bone muted text and accent (Bone buttons with Smoky Black text). State, edge, feature and
disruption marks are muted earthy hues (moss, rust, ochre, slate, plum, sage, clay), darker in light
and lighter in dark. Tokens: `--bg`, `--surface`, `--surface-2`, `--surface-3`, `--border`,
`--border-strong`, `--text`, `--text-muted`, `--text-faint`, `--accent`, `--on-accent`,
`--accent-glow`. Marks:

| token | use |
|---|---|
| `--relevant` / `--not-relevant` / `--save` / `--read` | paper states and the read mark |
| `--edge-cites` (blue, solid + arrow) | CITES |
| `--edge-similar` (violet, dashed) | SEMANTICALLY_SIMILAR — never styled or worded like a citation |
| `--edge-topic` (teal, dotted) | HAS_TOPIC |
| `--edge-authored` (purple, thin) | AUTHORED, scholar nodes |
| `--disruptive` (fuchsia) / `--consolidating` (sky) | disruption labels (always with a text label) |
| `--feat-question` `--feat-anchor` `--feat-draft` `--feat-citation` `--feat-preference` `--feat-recency` | score-breakdown segments, stacked in this order |

A vitest parses `tokens.css` and asserts ≥ 4.5:1 for text tokens and ≥ 3:1 for every mark on `--bg`,
`--surface`, `--surface-2`, `--surface-3` in both themes.

Typography: Inter Variable for UI (14–15 px base, tabular numbers); Source Serif 4 Variable for paper
titles (20–26 px on cards, 28–32 px in detail), abstracts and quoted findings (16–17 px, line-height
1.6, max 68ch). Shape: cards 20 px radius, panels 16, buttons 12, chips pill. Depth: soft shadows plus
a faint inner top highlight.

State presentation (`lib/states.ts` `STATE_META`): relevant → `--relevant`/check/"Relevant";
not_relevant → `--not-relevant`/x/"Not relevant"; saved → `--save`/bookmark/"Saved". The read mark is
`--read`/eye/"Read" (`READ_META`).

## 3. Theme and motion

- `lib/theme.ts`: `useTheme()` (`system | dark | light`, stored in `rr.theme`, default dark), applied
  as `<html data-theme>` before first paint by an inline script.
- `lib/motion.ts`: `useMotionPreference()` (`system | reduced | full`, `rr.motion`), `<html
  data-motion>`; `main.tsx` wraps the app in `<MotionConfig reducedMotion=…>`; Cytoscape layouts use
  `animate: !reduced`; under reduced motion the radar sweep is static and number counters jump.
- Springs: default `{ stiffness: 420, damping: 34 }`, gentle `{ stiffness: 260, damping: 30 }`. UI
  fades 150–220 ms. The radar sweep (2.4 s per turn) is the only infinite animation and only while a
  job runs. Page transitions: fade + 8 px rise. Lists stagger 30 ms (max 10 items). Charts draw in
  once (bars grow from the baseline, lines trace with `pathLength`), never loop.

## 4. App shell, routes, auth gate

- ≥ 1024 px: 248 px sidebar — logo lockup (22 px tall, links to `/welcome` — the landing page; `/` would redirect a signed-in user back into their project), project switcher (+ "New
  project"), project nav (Feed, Library, Graph, Documents, Project settings), workspace nav (Dashboard,
  Projects, Settings), theme switcher, privacy badge ("Private" — links to Settings → Privacy), user
  menu (avatar or initials, name, email, Settings, Sign out; hidden in local mode). 768–1023 px: icon
  rail with tooltips, the R mark on top. < 768 px: top bar (R mark, switcher, theme, avatar) + bottom
  tabs (Feed, Library, Graph, Dashboard, More → sheet with Documents, Project settings, Projects,
  Settings, Sign out). Touch targets ≥ 44 px; no horizontal scroll at 360 px.
- Auth gate: the app routes render under `RequireAuth`, which reads `GET /api/me` (`qk.me()`). A 401
  redirects to `/signin?next=<path>`; the client's `radar:unauthorized` event (any 401) clears the
  cache and does the same. In local mode `/signin` etc. redirect to `/`. Auth pages render in
  `AuthLayout` (no shell): the logo lockup (30 px, links home) above a centred card, over an animated
  radar backdrop. The landing page (§16) is public: `/` and `/welcome` sit outside `RequireAuth`.
- AppShell wraps the outlet in `<LayoutGroup id="paper">` and writes `rr.lastProjectId` on every
  `/projects/:projectId/*` route.

| Route | Page (`web/src/pages/`) |
|---|---|
| `/` | signed out (accounts mode, `me` → 401): `LandingPage`; signed in or local mode: redirect, `rr.lastProjectId` → its feed when it still exists, else `/projects` |
| `/welcome` | `LandingPage` for everyone (no session check) |
| `/signin`, `/signup`, `/forgot`, `/reset?token=`, `/verify?token=` | `SignInPage`, `SignUpPage`, `ForgotPasswordPage`, `ResetPasswordPage`, `VerifyEmailPage` |
| `/projects`, `/projects/new` | `ProjectsPage`, `NewProjectPage` |
| `/projects/:projectId` | redirect → `/projects/:projectId/feed` |
| `/projects/:projectId/feed` | `FeedPage` |
| `/projects/:projectId/library` | `LibraryPage` |
| `/projects/:projectId/graph/:paperId?` | `GraphPage` |
| `/projects/:projectId/documents` | `DocumentsPage` |
| `/projects/:projectId/settings` | `ProjectSettingsPage` |
| `/papers/:paperId?project=` | `PaperPage` |
| `/dashboard?days=&project=` | `DashboardPage` |
| `/settings` | `SettingsPage` |
| `*` | `NotFoundPage` |

## 5. Shared contracts

- `api/client.ts`: `apiFetch<T>(path, init)` — `credentials: "same-origin"`, `X-Radar-Client: web`
  on non-GET, the error envelope parsed into `ApiError {status, code, message, request_id,
  details}`, a window `radar:unauthorized` event on 401; `download(path)` (BibTeX); `upload<T>(path,
  file, {onProgress, signal})`; `setTransport(fn)` (tests).
- `api/hooks.ts`: the `qk` key factory and a hook per endpoint. `useFeedback(projectId)` serializes
  requests per project, applies the state optimistically to `qk.feed` and `qk.paper`, keeps a
  per-project undo stack `{interaction_id, paper_id, action, exit_direction}`, rolls back with a
  toast on error and invalidates project, library, graph, dashboard on settle. `useUndo(projectId)`
  pops it. `useJob(jobId)` polls every 1.5 s until the job settles. `useSetRead(paperId)` toggles the
  read mark optimistically.
- `features/paper/PaperDetailSheet.tsx`: `PaperDetailSheet({ paperId, projectId, slateId?, onClose })`,
  open iff `paperId !== null`, driven by the `?paper=<id>` search param (pushed, so Back closes it).
  Shared-element ids `paper-card-${id}` / `paper-title-${id}`. Only the sheet posts `open` (once per
  open, with a project).

## 6. Auth pages

- Sign in: "Continue with Google" / "Continue with GitHub" buttons (when enabled; full-page
  navigation to `/api/auth/oauth/{provider}/start?next=`), a divider, email + password form (when
  password login is on), links to sign up and "Forgot password?". `?error=` codes map to friendly text
  (`oauth_denied` "Sign-in was cancelled", `oauth_state` "That sign-in link expired — try again",
  `oauth_failed`, `no_verified_email` "Your provider account has no verified email", `not_allowed` "This
  email cannot use this server", `identity_in_use` "That account is linked to another user").
  `invalid_credentials` shows "Wrong email or password"; `rate_limited` shows the wait time.
  `email_unverified` (right password, email not confirmed yet) is not an error alert: a note "Confirm
  your email first. Open the link we sent to <email>…" with a "Resend email" button (signed-out
  resend by address, then a 60 s cooldown with "You can ask for another email in N s").
- Sign up: display name (optional), email, password with a live length meter (10+ characters) and
  show/hide; 409 → "An account with this email already exists" + Sign in link. When
  `email_verification` is on, a line under the button says a confirmation link will be emailed, and a
  `202 VerificationPending` answer replaces the form with "Check your inbox": the address, "No email?
  Check your spam folder, or send it again", "Resend email" (cooling down for 60 s from the start)
  and "Back to sign in" (keeps `next`). `next` is sent along so the link continues there. When
  `password_signup` is false the form is replaced by the Google/GitHub buttons and "Signing up with an
  email and password is not available on this server. Use Google or GitHub above…" ("Sign-up is
  closed on this server. Ask the administrator for an invitation." when no provider is enabled).
- Forgot: email → always "If an account exists for that address, a reset link is on its way" (when
  `email_delivery` is false: "Email is not set up on this server; ask the admin for a reset link").
- Reset: new password (token from the URL) → signed in → `/`. Verify: posts the token on mount
  (spinner "Checking your link…"); success signs in, toasts "Email confirmed — you are signed in" and
  continues to `next` (or `/`). `password_required` (link opened outside the browser that signed up)
  shows "Confirm your email": the email read-only (`autocomplete="username"`), a password field
  (autofocus, `current-password`), "Confirm and sign in", "Wrong password." inline on
  `invalid_credentials`, and "Reset the password" → `/forgot`. A bad link shows "This verification
  link is invalid or has expired" (or the `not_allowed` text) with "Send a new link": a button when
  signed in and unverified, else an email field (answer: "If an account is waiting for that address, a
  new link is on its way"), plus Sign in.
- Every form: inline field errors from `details.fields`, the submit button shows a spinner, Enter
  submits, autofocus on the first field, `autocomplete` attributes set.

## 7. Feed (hero screen)

Header: project name, date, progress `${decided} of ${items.length}` with a segmented bar (one segment
per item coloured by state), "Rebuild" and "More papers" (`{mode:"more"}`) buttons.

Deck: `slate.items` in rank order; the current card is the first item with `state === null`. The top
card plus two behind it (scale 0.96 / 0.92, y +12 / +24, dimmed); the next ones spring forward when
the top leaves. Card: chips (primary category with its name on hover, relative date, "Retracted",
"Open access", "Cited by N", read eye when marked), serif title (clamp 3), authors (first 3 + "et
al."), venue/year, abstract (clamp ~6 lines, "More" expands with a height animation), `reasons.slice(0,
3)` as pills (metadata reasons: check icon; model reasons: sparkle; full text on hover/focus; a keyword
match is a metadata pill, e.g. "Mentions your keyword ‘conformal prediction’ in the title"), footer
links (arXiv, PDF, DOI; `rel="noopener noreferrer"`) and "Why this paper?" (opens the sheet).

Drag physics: `rotate = x / 18` (±14°), lift 1.02; stamps fade in with distance — right "RELEVANT",
left "NOT RELEVANT", up "SAVE"; the border tints toward the action colour. Commit: |x| > 140 px or
|vx| > 650 → relevant / not relevant; y < −130 px or vy < −650 → save; else spring back. Exit: fly off
in the gesture direction (~320 ms); buttons and keys animate the same exit. Undo flies the card back
in from its exit side.

Action bar: circular buttons with icon + label: Not relevant (←), Undo (Z), Save (↑/S), Relevant (→),
Read (R, toggles the mark without leaving the deck). Keyboard (`lib/hotkeys.ts`, scope `deck`): the
keys above, Space/Enter open the detail, ? help. The sheet binds the same keys to its paper.

States: needs_setup (add categories → project settings); building (radar sweep + job message +
progress; overlays the dimmed previous deck); ready; empty (slate message + widen window / add
categories); not_built ("Build my first deck"); failed last job → error banner with Retry. A 409 on
rebuild shows "A deck is already being built". Deck complete: radar pulse, today's decisions by state,
"Explore connections" (graph), "More papers", "Come back tomorrow" — finite by design.

## 8. Paper detail sheet and paper page

Right-side sheet (min(640 px, 92vw); full-screen on mobile); `/papers/:id` renders the same content as
a page. Sections:
1. Header: title, authors (expandable; OpenAlex-linked authors open their scholar card), date, venue,
   categories, identifiers with copy buttons, links, read toggle.
2. "Why this paper" (with a project and a recommendation): metadata reasons ("From the sources") and
   model reasons ("Inferred by Ratiocination"), each expandable to its evidence (anchor paper link,
   draft filename/section/page/snippet or "a deleted document", category, days; for keywords each
   keyword, where it was found — title or abstract — and the paper's own matched words highlighted in
   `--feat-keyword`).
3. Score breakdown (collapsed): a stacked bar of the 7 feature contributions in `--feat-*` colours
   (question, anchor, keyword "Your keywords", draft, citation, preference, recency; `--feat-keyword`
   is brick red, placed between anchor and draft so no neighbouring segments merge under protan/deutan
   simulation, ≥ 3:1 on every surface in both themes), unavailable features hatched "not available",
   and a table (raw, value, weight, contribution). A deck built before keywords existed shows the
   keyword row as not available.
4. Abstract (serif) or "Abstract not available".
5. Disruption: `cd`, a three-part split bar (cite it alone `n_i` / cite both `n_j` / cite only its
   references `n_k`, the last on a log scale note), the label chip and `text`; "Compute" when missing
   (synchronous, with spinner; 422 → "Not in OpenAlex yet").
6. Breakthroughs of the authors: "Find the authors' breakthrough papers" → job (sweep + messages) →
   per author (name, institution, h-index) their analyzed works by CD with a "Breakthrough" badge;
   `message` shown when it stopped early.
7. Topics (OpenAlex) chips; neighborhood preview with "Open in graph" (with a project).
8. Sticky footer with the deck actions (with a project); Undo uses `last_interaction_id`.

## 9. Graph

Full-height Cytoscape canvas (`aria-hidden`), with a List view as its accessible equivalent
(default < 768 px): rows grouped by `groups`, each with Open, Expand and a "Links" disclosure.
A paper's neighbourhood (`/graph/:paperId`) is a wheel (`features/graph/layout.ts`): the paper in
the middle and its groups — references, cited by, similar papers, topics, authors with their most
disruptive papers indented under them — as runs of rows under small headers ("References · 25 of
40"), read down the left column, then the right one, and cut where the two heights balance,
preferring a cut between groups ("References (cont.)" when one splits). Labels point outwards, one
per row, so they never overlap; new nodes grow out of the middle. The project graph (`/graph`:
seeds, relevant and saved papers with shared topics, citations, similarity and shared authors) uses
fcose with label-aware spacing, new nodes starting next to a node they link to. Fitting stops at
1.25× zoom. Labels are short titles; hover or selection shows the full title. Legend always visible,
under the canvas. Nodes: works are circles sized by `weight` (smaller in the wheel), filled by state
colour (unrated: surface with an accent ring; seeds have a double ring); concepts rounded squares;
scholars and byline authors orange diamonds. Click a work → side panel (summary, state, actions,
Expand, Analyze breakthroughs); click a scholar → its works by CD; click an edge → panel (relation,
verified/inferred, confidence, provenance, citation contexts and intents). Expansion: a status pill
while `useJob` polls; the graph grows in place. Filter chips per relation; notices as an info banner.

## 10. Projects, wizard, documents

`/projects`: grid of project cards (name, question excerpt, stats, last deck time), "New project";
empty state "Create your first project". Wizard (`/projects/new`, slide + fade, step indicator):
1. Basics (name, research question, keywords, description). Keywords
(`features/projects/KeywordInput.tsx`): a chip input labelled "Keywords" with the helper "Papers that
mention these words in the title or abstract get a boost. Press Enter or comma to add one (n/12)";
Enter or comma adds the typed keyword (also on blur), Backspace in the empty field removes the last
chip, each chip has a remove button ("Remove keyword “x”"), pasting a comma/semicolon/line list adds
each item; inline errors for 2–60 characters, "already in the list" (case-, hyphen- and
plural-insensitive, the server's rule) and the limit of 12; a polite live region announces additions
and removals; server field errors (`keywords`, `keywords.<i>`) show under the field. The launch step
lists the keywords. 2. arXiv categories (searchable grouped list of
≥ 40 categories with names + validated free entry), time window, deck size → `POST /api/projects`
(`active: false`). 3. Seeds (optional): paste identifiers one per line (posted one at a time, each
animates in) or search the local library. 4. Private document (optional): dropzone, privacy note
"Processed on this server. The text never leaves it.", upload progress, status. 5. Build: PATCH
`active: true` → prepare build → sweep with live messages → feed. Project settings reuses the
sections (the keyword field sits in Basics between the question and the description and saves with
"Save basics") plus the seed list and a danger zone (delete with confirmation).

Documents: dropzone + privacy card; list with status chips (processing shimmer + message/progress,
polling while pending); outline accordion; Delete (disabled while pending) with a confirmation listing
what gets deleted and a toast with the returned counts; 409 "This file is already in the project".

## 11. Library

Tabs All / Relevant / Saved with counts from `project.stats`; read filter (Any / Unread / Read);
search; responsive grid of compact cards (state chip, read eye, CD chip when known); infinite scroll;
"Export BibTeX" (`download()`); clicking opens the sheet.

## 12. Dashboard

`/dashboard`: period segmented control (7 / 30 / 90 / 365 days, `?days=`), project filter (All
projects / one, `?project=`), the browser's time zone sent as `tz`. Sections (each loads on its own
with skeletons; empty states explain what fills them):
1. Pulse: KPI cards — Swiped, Relevant rate, Saved, Read, Streak (flame), Library — with animated
   counters and a delta chip vs the previous period (↑ green / ↓ red, "—" when no data).
2. Activity: bar chart per day (per week for 365 days), the positive part highlighted; hover/focus
   tooltip with the date and counts; a per-project split list.
3. Topics you lean into: "Leaning in" rows (label, share now vs before, delta chip, 12-week sparkline),
   "Fading", "Top topics" horizontal bars, "Often skipped" chips; coverage note when topics come from
   arXiv categories.
4. Main findings: the AI summary card (when available: "Summarize with Claude" with remaining
   requests, job progress, themes with cited paper chips, "AI-written from N abstracts · model ·
   date", a "Stale" badge with Refresh); quoted findings grouped by topic — serif quote cards with the
   cue words highlighted, the paper title (opens the sheet), the action chip and similarity.
5. Relation to your work (per project): the correlation sentence, quartile bars (relevant rate per
   similarity quartile), closest papers with what they matched (question / document section), section
   coverage (supporting counts; gaps highlighted "No liked papers support this section yet"), and the
   12-week drift sparkline.

## 13. Settings

Sections: Account (name, avatar initials, email + verified status / resend, password set or change,
connected accounts with Link/Unlink for Google/GitHub, "Sign out other devices", delete account with
email confirmation); Preferences (time zone select defaulting to the browser's, auto-expand switch);
Appearance (theme, motion); Privacy (the server's `privacy_disclosure`, AI summary status); Embedding
model (status: the active model with "Active" or "Fallback" — the fallback note names what is used
meanwhile, another downloaded model or the simpler built-in one; admins: "Download model (~67 MB from
Hugging Face)", the size of the configured model); Admin (scheduler switch + next run, connector
status, users, last daily run, "Run daily now").

## 14. Accessibility and quality

- Every action reachable by button and keyboard; visible focus rings (accent, 2 px offset); dialogs
  trap focus (Radix). Colour is never the only signal (icons, labels, line styles).
- Charts: each SVG has `role="img"` with an `aria-label` summary and a visually hidden table of the
  same numbers.
- Loading skeletons for content; the radar sweep only for jobs. No layout shift when cards change.
  Works at 360 px with no horizontal scroll. Reduced motion respected everywhere.

## 15. Code layout and checks

```
web/src/
  main.tsx                    providers (QueryClient, Router, MotionConfig, Toaster)
  app/                        router.tsx, AppShell.tsx, RequireAuth.tsx, AuthLayout.tsx, nav.ts, paths.ts, shell/
  api/                        types.ts, client.ts, hooks.ts
  components/ui/              Button, IconButton, Badge, Chip, Card, Sheet, Dialog, Tooltip, Tabs,
                              Switch, Slider, Skeleton, EmptyState, RadarSweep, BrandMark, ProgressBar,
                              Kbd, ConfirmDialog, SegmentedControl, Input, Menu, Popover, Spinner
  components/charts/          BarChart, Sparkline, StackedBar, SplitBar, AnimatedNumber
  styles/                     tokens.css, globals.css
  lib/                        theme, motion, hotkeys, states, format, storage
  features/<feature>/         auth, feed, paper, graph, projects, documents, library, dashboard, settings,
                              landing
  pages/                      one file per route
  test/                       setup, utils (renderWithProviders, installTestTransport)
```

Checks: `cd web && npm run typecheck && npm run test -- --run && npm run build`. Screenshots:
`npm run shots` drives the built app served by the backend with a demo database
(`scripts/demo_db.py`) at 1440×900 and 390×844 into `web/test-results/shots/`.

## 16. Landing page

Public marketing page (`pages/LandingPage.tsx`, `features/landing/`), lazy-loaded like every page,
with no API calls of its own. Reproduces the approved Figma design.

Routes: the index route's loader (`createRootLoader` in `app/router.tsx`) fetches `me` into
`qk.me()` (60 s fresh, no retry). A 401 (accounts mode, signed out) renders the landing page at `/`;
anything else — signed in, local mode (`me` always succeeds), or a failed check — takes the old root
redirect (last project's feed, else `/projects`), where `RequireAuth` reuses the cached answer and
shows its retry screen when the server is unreachable. `/welcome` renders the landing page for
everyone, without checking the session. Every other route keeps the auth gate, `next=` and the
`radar:unauthorized` handling (§4).

Links: nav HOW IT WORKS → `#how-it-works`, GRAPH → `#feature-graph`, DASHBOARD → `#feature-why`
(marketing anchors to the feature rows, not app links), SIGN IN → `/signin`; "Create an account" →
`/signup`, plus a quieter "Sign in"; footer GitHub → the repository (`rel="noopener noreferrer"`).

Look: Outfit Variable (`@fontsource-variable/outfit`, imported by the page so only its chunk loads it)
for UI and headings, Source Serif 4 for paper titles, category codes and the statement, the mono
stack for chips. Uppercase labels are 14 px bold. Layout: max 1440 px with 40 px gutters (content
1360), 16/24 px gutters on small screens, works from 360 px with no horizontal scroll, touch targets
≥ 44 px. Tokens in `tokens.css`: theme-invariant `--brand-*` (ink #11120D, accent #565449 Olive
Drab, accent-soft #D8CFBC Bone, white #FFFBF4 Floral White, tint #E8E0D2, paper, muted, hairline,
stamp and score-breakdown colours; `--brand-accent-deep` #4A483E for the cs.LG tile) and themed
`--lp-*` (page, text, muted, hairline, link, ink cards,
inverted pill buttons, panels, cards, chips, marks, graph edges); `contrast.node.test.ts` checks their
text and mark pairs in both themes. The page sets `<html class="lp-page">` while mounted so the
document background follows `--lp-bg`.

Sections, top to bottom:
1. Nav (reads `me`: signed in → avatar + "Open app" → `/`; signed out → "Sign in"; "Get started"
   likewise becomes "Your deck / Open my deck"): logo lockup (Smoky Black + Olive Drab, or Floral White + Bone, by theme; recoloured copies of
   `brand/logo` in `web/public/landing`), links in caps; below 768 px only How it works and
   Sign in remain, below 640 px only Sign in. A skip link leads to `#main`.
2. Hero: the lockup is the page's only h1 (alt "Ratiocination" + a visually hidden one-line
   description). The hero card (radius 30, 640 px tall at ≥ 1280 px, tint) is a light "photograph"
   in both themes: the portrait loop flush right/bottom (crop of the 1080 × 1486 frame to
   x 180–1080, y 440–1486: wrapper `aspect-ratio: 900/1046`, video 120 % wide anchored right/bottom,
   `mix-blend-mode: multiply` so the white frame takes the tint), the demo deck (three 460 × 300
   cards, scaled 0.64–1 by breakpoint; aria-hidden) and the scalloped "NEW · Fresh deck every day ·
   BETA" sticker (the default deck is 20 papers, so it names no number) (Floral White on Olive Drab, 7:1; real text for screen readers). Below 640 px
   the deck sits above the portrait and the sticker over the deck's lower-left corner.
3. What it is: eyebrow, 40/44 bold headline, ink pill (inverted in dark) → `#how-it-works`.
4. Category marquee: full-bleed 300 px tiles (220 px below 768 px) for cs.LG, cs.CL, q-bio.NC, stat.ML,
   cs.DL, rendered twice (the copy aria-hidden) for a seamless loop.
5. How it works: 50 px caps header, subtitle, a full-width 1 px rule, the FEATURES label (own 237 px
   column from 1360 px) and three rows separated by dotted rules — Swipe a daily deck, Know why every
   paper, Map the breakthroughs — each a 50/55 caps title and paragraph beside a 559 × 504 mock
   (aria-hidden, scaled to its column with `--fit`; stacked below 1024 px).
6. Statement: ink card (560 px), "ra·ti·oc·i·na·tion (n.)" and the 60 px serif line, radar scope.
7. Get started: eyebrow, headline, "Create an account" pill and "Sign in".
8. Footer: ink card (460 px) with rippling rings, the R mark (Floral White + Bone), "Private by
   default", "© 2026 Ratiocination", "Source code (AGPL)".

Dark theme (the default): the page, sections, tiles and mocks switch to the dark `--lp-*` values
(ink cards stay dark with a hairline ring; pill buttons invert to Bone); the hero card and its demo
cards keep their light brand colours.

Motion (all decorative; Motion values or CSS keyframes in `features/landing/landing.css`):
- Hero deck, 8 s loop (`lib/deckTimeline.ts`, pure keyframe tracks, unit-tested): A drags right with
  its RELEVANT stamp and flies out; B springs forward, drags up with SAVE and flies up; C moves to the
  middle, then the front, and wobbles left; from 6.95 s C eases back while B and then A rise from just
  below the hero card's bottom edge (measured) fully opaque — no cross-fades — into their slots with a
  gentle spring; 8 s equals 0 s. One requestAnimationFrame clock drives every card through Motion
  values and runs only while the hero is on screen and the tab is visible; the video pauses with it.
- Sticker: one clockwise turn per 16 s (the badge shape; the text stays upright).
- Marquee: slides left at ~100 px/s; pauses on hover, while focused, off screen, or with its
  pause/play button (WCAG 2.2.2), which hides under reduced motion.
- Feature mocks, when scrolled into view: the swipe card sways right as its stamp appears and the →
  button pulses (loops while visible); the score bar's six segments grow in sequence and the graph's
  edges draw out from the focus paper, nodes pop in and the focus node pulses (once).
- Statement radar: the sweep turns once per 8 s; four blips flare as it passes (negative
  `animation-delay` per blip angle). Footer: SVG rings ripple outward (14 s).
- Text blocks fade and rise 8 px on first view. Infinite CSS loops carry `lp-loop` and pause while
  their section is off screen (`data-offscreen`). No layout shift: only transforms and opacity move.

Reduced motion (the app's preference, which follows `prefers-reduced-motion` by default, via
`useMotionPreference()` and `<html data-motion="reduced">`): no clock, no video (the poster JPEG as a
still, same crop), every CSS loop off (globals.css) and each loop rests on a designed static frame
(deck stacked, sweep at one o'clock with lit blips, evenly spaced rings), mocks on their final frame,
no reveal fades, no marquee button.

Assets (`web/public/landing/`, copied from `brand/`): `ratiocination-logo.png` and
`ratiocination-logo-white.png` (1207 × 197, cropped to the artwork), `ratiocination-mark-white.png`
(205 × 197), `ratiocination-loop.webm` (VP9, 3.2 MB, first source), `ratiocination-loop-web.mp4`
(H.264, 4.5 MB, fallback), `ratiocination-loop-poster.jpg` (poster and reduced-motion still). The
video is `autoplay muted loop playsinline preload="metadata"` inside an aria-hidden wrapper, so the
poster paints first and the video never blocks first paint. The demo papers on the mocks are made up
(`features/landing/content.ts`).

Tests: `app/router.test.tsx` ("landing page" block), `features/landing/landing.test.tsx`,
`features/landing/lib/deckTimeline.test.ts`, `e2e/landing.spec.ts`.
