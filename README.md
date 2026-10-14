# Ratiocination

A research discovery website. For each research project it builds a short daily deck of new arXiv
papers, ranked against your research question, your seed papers, your draft and your ratings; you
swipe through it (relevant, not relevant, save). Every card says why it was picked. Around the
papers you keep it grows a knowledge graph (citations, similar papers, topics, authors), measures
how disruptive a paper was (the CD index) and finds the authors' breakthrough papers. A personal
dashboard shows what you read and swiped, the topics you are leaning into, what the papers you kept
found (quoted from their abstracts, optionally summarised by Claude) and how they relate to your work.

People sign in with Google, GitHub or email + password; every project, rating, document and summary
is private to its owner.

- Backend: Python 3.13, FastAPI, SQLAlchemy 2, Alembic, SQLite (PostgreSQL-compatible code).
- Frontend: React 19 + Vite + TypeScript + Tailwind v4 + Motion + Cytoscape (`web/`).
- Contract and design: [`docs/SPEC.md`](docs/SPEC.md), [`docs/API.md`](docs/API.md),
  [`docs/UI.md`](docs/UI.md), [`docs/CONVENTIONS.md`](docs/CONVENTIONS.md), decision log
  [`docs/DECISIONS.md`](docs/DECISIONS.md).

## Quickstart

Requirements: Python 3.13 and Node.js.

```bash
# 1. Backend (repo-root virtualenv)
python3.13 -m venv .venv
.venv/bin/python -m pip install -e "backend[dev]"

# 2. Frontend
(cd web && npm ci)

# 3. Recommended: download the local embedding model (one explicit download of public model files;
#    until then the simpler local/hashing-v1 embedder is used)
.venv/bin/python -m radar.cli download-model

# 4. Run backend (http://127.0.0.1:8000) + Vite dev server (http://localhost:5173)
RADAR_PUBLIC_URL=http://localhost:5173 scripts/dev.sh
```

Open http://localhost:5173 and create an account; the first account becomes the administrator.
With a production build (`cd web && npm run build`) the backend also serves the app at
http://127.0.0.1:8000.

For a single person on one machine, `RADAR_AUTH=local` skips sign-in entirely.

To run only the backend:

```bash
cd backend
../.venv/bin/uvicorn radar.main:create_app --factory --host 127.0.0.1 --port 8000
# or: ../.venv/bin/python -m radar.cli serve
```

Configuration lives in environment variables or a repo-root `.env` file; [`.env.example`](.env.example)
lists every option. Runtime data (database, uploads, model cache, logs) goes to `data/`
(gitignored) unless `RADAR_DATA_DIR` says otherwise.

### Google and GitHub sign-in

Create an OAuth client with the provider and set its id and secret:

- Google (OAuth client of type "Web application"): redirect URI
  `<RADAR_PUBLIC_URL>/api/auth/oauth/google/callback`; set `RADAR_GOOGLE_CLIENT_ID` and
  `RADAR_GOOGLE_CLIENT_SECRET`.
- GitHub (OAuth app): callback URL `<RADAR_PUBLIC_URL>/api/auth/oauth/github/callback`; set
  `RADAR_GITHUB_CLIENT_ID` and `RADAR_GITHUB_CLIENT_SECRET`.

Only verified email addresses are accepted from providers. `RADAR_ALLOWED_EMAILS` limits who may
sign up. Verification and password-reset emails need `RADAR_SMTP_*`; without SMTP an admin can print
a reset link with `.venv/bin/python -m radar.cli reset-link --email <address>`, or invite someone who has
no account yet with `.venv/bin/python -m radar.cli invite-link --email <address>` (prints a one-time link
to set a password; it also confirms the address). On a public server, email + password sign-up waits
for the emailed confirmation link, so it opens only once SMTP works.

### AI summaries (optional)

Set `ANTHROPIC_API_KEY` to enable "Summarize with Claude" on the dashboard. Only the public titles
and abstracts of the papers you kept are sent; every point in the summary cites its papers, and
points that cite nothing are dropped. Without a key the dashboard shows verbatim quotes only.

## Command line

```bash
.venv/bin/python -m radar.cli --help
```

Commands: `init-db`, `serve`, `reset-link --email`, `invite-link --email`, `daily`, `ingest-feeds`, `backfill --project N`,
`enrich`, `embed`, `build-deck --project N`, `expand --paper N`, `download-model`.

## Deploy

The `Dockerfile` builds one container (web app + backend, data on a volume at `/data`); `fly.toml`
runs it on Fly.io as a single always-on machine. Step-by-step runbook (secrets, OAuth callbacks,
email, custom domain, backups, updates): [`docs/DEPLOY.md`](docs/DEPLOY.md).

## Tests

```bash
cd backend && ../.venv/bin/pytest -q          # backend (network blocked, strict transactions)
cd backend && ../.venv/bin/ruff check radar tests && ../.venv/bin/ruff format --check radar tests
cd web && npm run typecheck && npm run test -- --run && npm run build
cd web && npm run e2e                         # browser checks: real backend (offline) + production build
```

Backend tests never touch the real `data/` directory: every test gets a fresh temporary data
directory and database (`backend/tests/conftest.py`). Frontend tests stub the API per test.

Screenshots of a running server (desktop and mobile, dark and light):
`cd web && npm run shots -- --base http://127.0.0.1:8000` (PNGs in `web/test-results/shots/`).

## Privacy

- **Private to you:** project names, research questions, keywords, descriptions, uploaded drafts (file, text,
  sections, passages and their vectors), ratings, read marks, library, dashboard and AI summaries.
  No other user can see them, and they are never sent to another service.
- **What providers see:** looking up public papers reveals which papers were looked up to arXiv,
  OpenAlex and Semantic Scholar — seeds you add, papers whose citations load (automatically when you
  mark a paper relevant, if enabled) and breakthrough analyses. Your contact email is sent to
  OpenAlex only if you set `RADAR_CONTACT_EMAIL`. With `ANTHROPIC_API_KEY` set, asking for an AI
  summary sends the public titles and abstracts of the papers you kept to Anthropic.
- **The only other egress** is the explicit embedding-model download. `RADAR_OFFLINE=1` blocks all
  outbound HTTP.
- **Sign-in:** passwords are hashed with Argon2id; sessions are HttpOnly cookies (`Secure` behind
  https); OAuth uses state + PKCE; sign-in attempts are rate limited.
- **Network exposure:** the server binds to 127.0.0.1 by default. A public server (a non-loopback bind,
  `RADAR_BEHIND_TLS_PROXY=1`, or a non-localhost `RADAR_PUBLIC_URL`) refuses to start without
  `RADAR_AUTH=accounts`, `RADAR_ADMIN_EMAILS`, TLS (`RADAR_TLS_CERT` + `RADAR_TLS_KEY`, or
  `RADAR_BEHIND_TLS_PROXY=1`) and an https `RADAR_PUBLIC_URL`. Host headers are checked,
  state-changing requests need the `X-Radar-Client: web` header and an allowed `Origin`, request bodies
  are capped (64 KB; uploads at `RADAR_MAX_UPLOAD_MB`), and responses carry `Referrer-Policy:
  no-referrer`, `nosniff`, `X-Frame-Options: DENY`, a Content-Security-Policy and, over https, HSTS.
- **Deletion:** deleting a document, a project or your account removes its files, rows and vectors
  (`secure_delete` is on). Logs never contain private text, emails, passwords, tokens or keys.

## License

Ratiocination is free software under the [GNU Affero General Public License v3.0 or later](LICENSE).
If you run a modified version for other people over a network, the AGPL asks you to offer them its
source code; the site links to this repository from the landing page footer and the sign-in pages.
The Ratiocination name, logo and the artwork in `brand/` are not covered by the licence.
