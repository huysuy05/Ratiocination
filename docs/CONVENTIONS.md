# Build conventions v0.2 (read before writing code)

These rules exist because many agents build this repo in parallel. Follow them exactly.

## Repository layout

```
backend/                 Python package `radar` (FastAPI). Python 3.13, repo-root .venv
  pyproject.toml
  alembic.ini
  migrations/            Alembic environment + versions
  radar/                 application package (module map: docs/SPEC.md §2)
  tests/                 pytest suite; tests/fixtures/ holds real-format payloads (see its README)
web/                     React SPA (Vite + TypeScript + Tailwind v4 + Motion)
docs/                    SPEC.md (what to build), API.md (REST contract), UI.md (design),
                         CONVENTIONS.md (this file), DECISIONS.md (append-only log)
data/                    runtime data, gitignored: radar.db, uploads/, models/, logs/
scripts/                 dev helpers (scripts/dev.sh starts backend + frontend)
```

## Environment

- Repo root: `/Users/huy.suy05./Documents/Projects/Ratiocination`.
- Python: the repo-root virtualenv `.venv` (Python 3.13). Always call `.venv/bin/python`,
  `.venv/bin/pytest`, `.venv/bin/alembic`, `.venv/bin/ruff` by path.
- Node: run npm inside `web/`.
- Dependencies are installed only by foundation agents. Do not `pip install` or `npm install`
  new packages afterwards. If you truly need one, list it under `deps_needed` in your report and
  work around it.
- Backend tests: `cd backend && ../.venv/bin/pytest -q` (one file: add its path).
- Frontend checks: `cd web && npm run typecheck && npm run test -- --run && npm run build`.
- Never read or write the real `data/` directory from tests; tests use temporary directories (the
  `conftest.py` fixtures do this). Exception: `data/models/` is a model cache that the
  `download-model` command may populate.
- Servers: if you start a server for manual checks, use the port assigned in your task, run it in the
  background, use a temporary `RADAR_DATA_DIR`, and kill it before you finish.

## Stub-first contracts

The foundation phase creates every module listed in SPEC §2, with every public function, class
and constant named in SPEC/API having its exact signature and a docstring. Bodies of functions
owned by a later agent raise `NotImplementedError("owned by <agent>")`. Lifecycle hooks are no-ops.
Owners replace bodies; they never rename or change a signature. If a signature must change, do not
change it — record it under `contract_deviations`.

## File ownership

- Every agent edits only the files its task lists as owned (creating new files inside owned
  directories is fine).
- Frozen after the foundation phase (nobody edits them later):
  `backend/pyproject.toml`, `backend/alembic.ini`, `backend/migrations/**`, `backend/radar/config.py`,
  `backend/radar/db.py`, `backend/radar/models.py`, `backend/radar/schemas.py`,
  `backend/radar/errors.py`, `backend/radar/main.py`, `backend/radar/logging_setup.py`,
  `backend/radar/api/deps.py`, `backend/radar/api/serializers.py`, `backend/radar/store/**`,
  `backend/radar/net/**`, `backend/radar/embeddings/**`, `backend/radar/jobs/runner.py`,
  `backend/radar/jobs/registry.py`, `backend/radar/jobs/scheduler.py`,
  `backend/radar/jobs/triggers.py`, `backend/tests/conftest.py`;
  `web/package.json`, `web/index.html`, `web/vite.config.ts`, `web/tsconfig*.json`,
  `web/vitest.config.ts`, `web/playwright.config.ts`, `web/src/main.tsx`, `web/src/api/**`,
  `web/src/app/**`, `web/src/components/ui/**`, `web/src/styles/**`, `web/src/lib/**`,
  `web/src/test/**`, `web/src/mocks/{index,db,router,scenarios}.ts`.
  If you need a change in a frozen file, do not edit it: work around it inside your own files and
  describe the needed change under `contract_deviations` in your report.
- `docs/DECISIONS.md` is append-only for everyone. Append with a heredoc so concurrent writers do not
  clobber each other: `cat >> docs/DECISIONS.md <<'EOF'` … `EOF`. Format: `- YYYY-MM-DD [module]
  decision — reason`.

## Database rules (SQLite is single-writer; read SPEC §2.1)

- Use `radar.db.session_scope(write=True)` for any session that writes and
  `session_scope()` (read) otherwise. Never write in a read session (strict mode raises).
  FastAPI routes use the `get_db` (read) or `get_write_db` (write) dependency.
- Route handlers that touch the DB are sync `def`, never `async def`.
- Never hold a write transaction across slow work: do HTTP calls, embedding and ranking first, then
  write in a short write session, committing per batch (≤200 rows or ≤1 s).
- Never call `HttpClient.request`, a connector, or `submit_job(session=None)` while your thread holds
  an open write transaction; strict mode (`RADAR_STRICT_TX=1`, on in tests) raises. To queue a job
  from a write path, pass your session: `submit_job(..., session=session)` (dispatch happens after
  commit).
- Every check-then-insert helper (`upsert_paper`, `upsert_edge`, `get_or_create_concept`, …) runs
  inside a write session.
- JSON columns are immutable values: copy, modify, reassign
  (`p.settings_json = {**p.settings_json, "k": v}`) or call `flag_modified`. Never mutate in place.
- Never filter or compare on JSON contents in SQL (not portable between SQLite and Postgres). Add a
  real column for anything you need to query.
- All datetime columns use `radar.models.UTCDateTime`; only aware UTC datetimes go in or come out.

## Python style

- Type hints everywhere. SQLAlchemy 2.0 typed ORM (`Mapped[...]`, `mapped_column`). Pydantic v2.
- Timezone-aware UTC datetimes everywhere (`datetime.now(UTC)`); serialize as ISO 8601 with `Z`.
- Keep pure parsing/scoring functions separate from I/O so they can be unit-tested.
- Logging via `logging.getLogger(__name__)`. Never log private text: document text, chunk text,
  snippets, headings, filenames, project names, research questions, descriptions, user topic labels,
  excluded terms, search queries, or private vectors. Log IDs and counts. Exception messages that may
  be stored or returned must not contain URLs with query strings, keys or emails.
- All outbound HTTP goes through `radar.net.http` (never `httpx`/`requests` directly elsewhere). The
  only other egress is the explicit embedding-model download (SPEC §9).
- No pickle for persisted models; store arrays as float32 bytes or JSON.

## TypeScript / React style

- `strict` TypeScript, no `any` (use `unknown` and narrow). Function components and hooks.
- Styling with Tailwind utilities that reference the theme CSS variables in `web/src/styles/`.
- Animation with Motion (`import { motion } from "motion/react"`). Respect the motion preference
  (`useMotionPreference` from `web/src/lib/motion.ts`).
- Server data only through the hooks in `web/src/api/hooks.ts` (or your own hooks built on
  `apiFetch` + the `qk` key factory, placed in your feature folder).
- API JSON uses snake_case; TypeScript types mirror it exactly (`web/src/api/types.ts`).
- Every interactive control is a real `<button>`/`<a>`/input with an accessible name. Swipe is never
  the only way to do something. External links use `rel="noopener noreferrer"`.
- New helpers go in `web/src/features/<your feature>/lib/`, not in shared folders.

## Tests

- Backend: pytest. Network is blocked (`RADAR_OFFLINE=1`) and strict transactions are on
  (`RADAR_STRICT_TX=1`). Mock HTTP with the `http_mock` fixture (an `httpx.MockTransport`). Tests use
  the deterministic `local/hashing-v1` embedder. Fixtures are trimmed real payloads; any synthetic
  record must be obviously synthetic (e.g. title "Test paper A").
- Frontend: vitest + Testing Library; mock the API module or use the mock transport.
- Each module ships tests for its acceptance criteria (SPEC §19). Tests must be deterministic.

## Reports

Each agent ends with the JSON report its workflow schema asks for: summary, files touched, the exact
test commands run and their results, contract deviations, open issues, deps needed. Report failures
honestly; never claim a test passed that you did not run.
