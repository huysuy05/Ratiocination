# Deploying Ratiocination on Fly.io

A runbook for the first public deployment and for running it afterwards. You type every `fly` command yourself; nothing here
was run against Fly. The container contract (environment, volume, port, health endpoint) is in [SPEC.md](SPEC.md) "Deployment".

What you get: **one** Fly Machine in Chicago (`ord`, shared-cpu-1x, 2 GB RAM) running the image built from `Dockerfile`, a 3 GB
volume mounted at `/data` (database, uploads, embedding model, logs) with daily snapshots, and HTTPS on
`https://<app>.fly.dev` (later your own domain). It must stay exactly one machine: the app keeps SQLite on the volume and runs
its job queue and daily scheduler in-process.

Files involved: `Dockerfile`, `.dockerignore`, `fly.toml`, `backend/requirements.lock.txt` (pinned Python dependencies),
`scripts/docker-entrypoint.sh` and `scripts/container-radar.sh` (the `radar` command inside the container).

Placeholders below: app name `ratiocination`, admin email `you@example.org`, custom domain `radar.example.org`.

## 0. Before you start

1. A Fly.io account with a payment card on file (https://fly.io/app/sign-up).
2. `brew install flyctl`, then `fly auth login`.
3. No Docker needed: `fly deploy` uploads the repository (filtered by `.dockerignore`) to Fly's remote builder.
4. Decide:
   - **App name.** Names are global across Fly, so `ratiocination` may be taken. If `fly apps create` refuses it, pick another and
     change `app` and `RADAR_PUBLIC_URL` at the top of `fly.toml` to match.
   - **Admin email(s).** The server refuses to start on a public address without `RADAR_ADMIN_EMAILS`. Only those addresses become
     administrators; everyone else who signs up is a normal user.
   - **How people sign in.** On a public server, email + password sign-up requires a confirmed email address, so it only opens once
     outgoing email (SMTP, section 5) works. Google and GitHub sign-in work without SMTP. Set up at least one of: Google,
     GitHub, SMTP. The quickest first launch is GitHub (5 minutes, no review). Without any of them you can still create password
     accounts by hand, one link at a time (`radar invite-link`, section 4).

## 1. First deploy

From the repository root:

```bash
fly apps create ratiocination                       # or your name; then edit fly.toml (app, RADAR_PUBLIC_URL)
fly volumes create radar_data --region ord --size 3 --snapshot-retention 14
```

`fly volumes create` warns that a single volume has no redundancy: answer `y`. That is expected for this app (one machine,
one SQLite database); snapshots (section 7) are the safety net.

Set the secrets. `--stage` stores them without restarting anything (there is nothing to restart yet):

```bash
fly secrets set --stage RADAR_ADMIN_EMAILS="you@example.org"
# at least one sign-in method (section 3 and 5 explain where the values come from):
fly secrets set --stage RADAR_GITHUB_CLIENT_ID="..." RADAR_GITHUB_CLIENT_SECRET="..."
fly secrets set --stage RADAR_GOOGLE_CLIENT_ID="..." RADAR_GOOGLE_CLIENT_SECRET="..."
# during a closed beta, limit who may sign up (addresses and/or @domains):
fly secrets set --stage RADAR_ALLOWED_EMAILS="you@example.org,@your-university.edu"
```

Then build and start:

```bash
fly deploy
```

The first build takes several minutes (Node builds the web app, then the Python image). Fly then creates one machine, attaches
the volume, the app runs its database migrations, and Fly waits until `GET /api/health` answers. Check:

```bash
fly status          # one machine, state "started", checks passing
fly machine list    # exactly ONE machine; if you ever see two: fly scale count 1
fly logs            # live log; Ctrl-C to leave
fly open            # opens https://ratiocination.fly.dev
```

(`fly launch --no-deploy --copy-config --name <app>` is an alternative to `fly apps create`; if it offers databases or other
extras, decline them.)

## 2. Secrets and settings

Non-secret settings live in `fly.toml` `[env]` (change them there, then `fly deploy`). Everything below is a secret
(`fly secrets set NAME="value"`; each call restarts the machine unless you add `--stage`; `fly secrets list` shows names only).

| Secret | Needed | What |
|---|---|---|
| `RADAR_ADMIN_EMAILS` | **yes** | Admin addresses, comma separated. Without it the server refuses to start. |
| `RADAR_GITHUB_CLIENT_ID`, `RADAR_GITHUB_CLIENT_SECRET` | one sign-in method | GitHub sign-in (section 3) |
| `RADAR_GOOGLE_CLIENT_ID`, `RADAR_GOOGLE_CLIENT_SECRET` | one sign-in method | Google sign-in (section 3) |
| `RADAR_SMTP_HOST`, `RADAR_SMTP_PORT`, `RADAR_SMTP_USERNAME`, `RADAR_SMTP_PASSWORD`, `RADAR_SMTP_FROM`, `RADAR_SMTP_SECURITY` | for email + password accounts | Verification and password-reset emails (section 5) |
| `RADAR_ALLOWED_EMAILS` | recommended for a beta | Who may sign up: `name@x.org` and/or `@x.org`, comma separated. Unset = anyone. |
| `ANTHROPIC_API_KEY` | optional | "Summarize with Claude" on the dashboard (public titles and abstracts only). Capped at `RADAR_SUMMARIES_PER_DAY` (5) per user and `RADAR_SUMMARIES_GLOBAL_PER_DAY` (50) for everyone per UTC day; change those in `[env]` |
| `OPENALEX_API_KEY` | optional | Higher OpenAlex budget |
| `S2_API_KEY` | optional | Semantic Scholar key |
| `RADAR_CONTACT_EMAIL` | optional | OpenAlex polite-pool contact; sent to OpenAlex only when set |

The full list of settings, with defaults, is in [`.env.example`](../.env.example) and SPEC §3.

## 3. Google and GitHub sign-in

The callback URLs must match `RADAR_PUBLIC_URL` exactly (https, no trailing slash).

**GitHub** (https://github.com/settings/developers, "OAuth Apps", "New OAuth App"):

- Homepage URL: `https://ratiocination.fly.dev`
- Authorization callback URL: `https://ratiocination.fly.dev/api/auth/oauth/github/callback`
- After creating it, "Generate a new client secret". Set `RADAR_GITHUB_CLIENT_ID` and `RADAR_GITHUB_CLIENT_SECRET`.
- A GitHub OAuth app has a single callback URL, so keep a separate OAuth app for local development.

**Google** (https://console.cloud.google.com, a project of your own):

1. "Google Auth Platform" / "OAuth consent screen": user type External; app name; your support email. The app only asks for
   `openid email profile`, which needs no Google review. While the publishing status is "Testing", only the test users you list
   can sign in: add yourself, or click "Publish app" when you open the beta.
2. "Clients" / "Credentials", "Create OAuth client ID", type **Web application**. Authorized redirect URI:
   `https://ratiocination.fly.dev/api/auth/oauth/google/callback`.
3. Set `RADAR_GOOGLE_CLIENT_ID` and `RADAR_GOOGLE_CLIENT_SECRET`.

Only verified email addresses are accepted from either provider.

## 4. First admin account

1. Make sure your address is in `RADAR_ADMIN_EMAILS` (it is checked again at every sign-in) and, during the beta, admitted by
   `RADAR_ALLOWED_EMAILS`.
2. Either sign in with Google or GitHub using that address, or create a password account. Without SMTP, print a one-time link on
   the server:

   ```bash
   fly ssh console -C "radar invite-link --email you@example.org"
   ```

   It creates the account (without a password) if it does not exist yet and prints `https://<your app>/reset?token=…`. Open it
   within 30 minutes and choose a password: that confirms the address and signs you in. The link works once; run the command
   again for a new one. (With SMTP you can instead sign up normally and open the confirmation link the app emails you.)
   On a public server admin rights need a **confirmed** address: Google and GitHub confirm it for you; a password account becomes
   admin once its address is confirmed by the invite link, a reset link or the confirmation email.
3. Settings now shows the admin section (scheduler, embedding model, connector status). The first confirmed account whose email
   is in `RADAR_ADMIN_EMAILS` also becomes user 1.

`radar invite-link` works for any beta tester too (exit code 1 when password login is off or the address is not in
`RADAR_ALLOWED_EMAILS`, 2 for an invalid address). If you lock yourself out of an existing password account and have no SMTP yet:
`fly ssh console -C "radar reset-link --email you@example.org"` prints a one-time reset link (30 minutes).

## 5. Email (SMTP)

Needed for email + password accounts (confirmation links) and "forgot password". Use any transactional email provider that
offers SMTP: create an account, verify your sending domain (it gives you SPF/DKIM DNS records to add) or a single sender
address, and create SMTP credentials. Then:

```bash
fly secrets set RADAR_SMTP_HOST="smtp.provider.example" RADAR_SMTP_PORT="587" RADAR_SMTP_SECURITY="starttls" \
  RADAR_SMTP_USERNAME="..." RADAR_SMTP_PASSWORD="..." RADAR_SMTP_FROM="Ratiocination <no-reply@your-domain.org>"
```

Use port 587 with `starttls` (or 465 with `ssl`); port 25 is often blocked. Test it with a second address: sign up, the
confirmation email arrives, the link signs you in; then "Forgot password" on the sign-in page.

## 6. Custom domain

1. `fly certs add radar.example.org`, then create the DNS records it prints (for a subdomain usually a CNAME to
   `ratiocination.fly.dev`). `fly certs show radar.example.org` until the certificate is issued.
2. In `fly.toml` set `RADAR_PUBLIC_URL = "https://radar.example.org"`, then `fly deploy`. The public URL builds OAuth redirect
   URIs and email links, decides the allowed Host and Origin, and its https makes the session cookie `Secure`.
3. Update the OAuth apps: Google, add `https://radar.example.org/api/auth/oauth/google/callback` as a redirect URI; GitHub,
   change the callback URL to `https://radar.example.org/api/auth/oauth/github/callback` (and the homepage URL).
4. The old `ratiocination.fly.dev` address now answers "Invalid host header". To keep it working too, add
   `RADAR_ALLOWED_HOSTS = "ratiocination.fly.dev"` to `[env]`. Session cookies are per host, so people sign in again on the new
   domain.

The health check sends `Host: localhost`, so it keeps passing through renames and domain changes.

## 7. Backups

**Volume snapshots (automatic).** Fly snapshots the whole volume daily and keeps each snapshot 14 days (`snapshot_retention`
in `fly.toml`; set at creation with `--snapshot-retention`). Snapshots are incremental and billed by stored size. A daily
snapshot can be up to a day old, so take one by hand before risky changes:

```bash
fly volumes list                              # the volume id (vol_...)
fly volumes snapshots list vol_...            # existing snapshots
fly volumes snapshots create vol_...          # on demand, e.g. before a deploy with migrations
```

**On-demand database copy.** A consistent copy of the SQLite database, taken while the app runs, downloaded to your computer:

```bash
fly ssh console -C "radar backup"             # prints /data/backups/radar-<UTC time>.db
fly ssh sftp get /data/backups/radar-<UTC time>.db ./radar-<UTC time>.db
fly ssh console -C "rm /data/backups/radar-<UTC time>.db"
```

The copy holds every user's private data and password hashes: keep it encrypted and delete old copies. (Uploaded files and
the model are not in it; they live next to it on the volume and are covered by snapshots.)

**Restoring a snapshot.** This rolls the whole volume back to the snapshot; everything written after it is lost.

```bash
fly volumes list                              # current volume id (vol_old)
fly volumes snapshots create vol_old          # safety snapshot of the current state first
fly volumes snapshots list vol_old            # choose the snapshot id (vs_...)
fly machine list                              # current machine id (m_old)
fly scale count 2 --with-new-volumes --from-snapshot vs_... --region ord
fly machine destroy m_old --force             # back to ONE machine, now on the restored volume
fly machine list && fly volumes list          # one machine; vol_old is unattached
```

For the minute or two between the last two commands, two machines answer requests; that is the only time it is acceptable.
Keep `vol_old` (now unattached) until you are sure, then `fly volumes destroy vol_old`.

## 8. Logs and monitoring

- `fly logs` streams the app log (uvicorn access lines without query strings, job and scheduler messages; secrets are
  redacted). `fly logs --no-tail` prints recent lines and exits.
- The app also keeps `/data/logs/radar.log` (rotated, 3 × 5 MB).
- `fly status`, `fly checks list`, and the Metrics tab of the app in the Fly dashboard (memory, CPU, CPU throttling).
- "Out of memory: Killed process" in the logs means the machine needs more memory (section 11).

## 9. Updating

```bash
cd backend && ../.venv/bin/pytest -q && cd ..   # tests first
fly volumes snapshots create vol_...             # when the release includes a database migration
fly deploy
```

`fly deploy` builds from your **working tree** (not from git), so uncommitted changes ship too. The image runs `npm ci` and
`npm run build` itself, so `web/package-lock.json` must be up to date. When Python dependencies change in
`backend/pyproject.toml`, regenerate `backend/requirements.lock.txt` (instructions at its top). The machine is replaced in
place: expect 10 to 30 seconds of downtime. Migrations run automatically at startup.

## 10. Rollback

```bash
fly releases --image                            # previous releases and their image references
fly deploy --image registry.fly.io/<app>:deployment-...
```

Database migrations only go forward. If the release you are rolling back from changed the database schema, older code may
not start against it: restore the snapshot you took before that deploy (section 7) instead.

## 11. Sizing and cost

Measured with the production settings on a copy of this image's runtime (macOS, Python 3.13, the pinned dependencies):

| Situation | Memory (RSS) |
|---|---|
| Started, idle | ~0.14 GB |
| Embedding model loaded (the first health check after a start loads it, once downloaded) | ~0.37 to 0.44 GB |
| After the first deck of a project (embeds up to 2,000 papers; ONNX Runtime keeps its working memory) | ~1.0 to 1.1 GB |
| Worst case: abstracts at the model's 512-token limit | ~1.55 GB |

Hence 2 GB of memory (plus 512 MB swap). 1 GB would run out of memory on the first deck.

CPU: embedding is the heavy part. The first deck of a new project embeds up to 2,000 papers, about 460 CPU-seconds on an Apple
M1 core; later decks reuse the vectors (under a second), and the daily run only embeds that day's new papers. A shared CPU on
Fly runs at full speed only while it has burst credit (at most about 500 seconds of it), then at 1/16 of a core. So a new
project's first deck can take from several minutes to more than an hour. If that matters, switch to a dedicated CPU: in
`fly.toml` set `[[vm]] size = "performance-1x"` (keep `memory = "2gb"`) and `fly deploy`. It costs noticeably more.

Cost: one always-on machine, one volume, snapshot storage and outbound traffic. Prices change; check https://fly.io/pricing
before you launch and keep an eye on the billing page during the first month.

## 12. Running commands on the server

`fly ssh console` opens a root shell on the machine; `fly ssh console -C "<command>"` runs one command. Use the `radar` command
there. It runs the Ratiocination CLI as the app's user, so files it creates on `/data` stay writable for the app:

```bash
radar invite-link --email someone@example.org  # one-time link to set a password (creates the account; no SMTP)
radar reset-link --email someone@example.org   # one-time password reset link
radar download-model                           # same as the button in Settings (the app picks it up within 30 s)
radar daily                                    # run the daily pipeline now
radar backup                                   # section 7
radar --help
```

Avoid running `python -m radar.cli` directly as root. (If you do, the files it creates are owned by root until the next restart,
which fixes their ownership.)

## 13. Pre-launch checklist

- [ ] `RADAR_ADMIN_EMAILS` holds your address(es); you signed in and see the admin section in Settings.
- [ ] `RADAR_ALLOWED_EMAILS` restricts sign-up during the beta (or you decided to leave sign-up open).
- [ ] OAuth callback URLs match `RADAR_PUBLIC_URL` exactly; Google consent screen published or test users added.
- [ ] Email: sign-up confirmation, "forgot password" and the reset link work end to end with a second address.
- [ ] Wrong passwords get throttled ("Too many failed sign-ins") and you can still sign in after 15 minutes.
- [ ] Embedding model downloaded (Settings); a test project builds its first deck.
- [ ] Exactly one machine (`fly machine list`); `fly checks list` passing; `fly logs` free of errors.
- [ ] Security headers: `curl -sI https://ratiocination.fly.dev/` shows `strict-transport-security`, `x-frame-options: DENY`,
      `x-content-type-options: nosniff` and a `content-security-policy` with one `'sha256-…'`; `curl -sI
      https://ratiocination.fly.dev/api/health` shows `cache-control: no-store`.
- [ ] Client IPs are real: `curl -s -o /dev/null -H "Fly-Client-IP: 203.0.113.9" https://ratiocination.fly.dev/api/health`,
      then the matching line in `fly logs` shows **your** IP, not 203.0.113.9 (Fly sets this header itself; the per-IP sign-in
      limit depends on it).
- [ ] `fly ssh console -C "radar backup"` works; after a day, `fly volumes snapshots list` shows a snapshot.
- [ ] The daily scheduler is on (Settings, admin section).

## 14. Troubleshooting

| Symptom | Cause and fix |
|---|---|
| Machine restarts; log says `Refusing to start: ... RADAR_ADMIN_EMAILS` | Set the secret (section 2). |
| `Refusing to start: RADAR_AUTH=accounts needs a sign-in method` | Password login was turned off and no complete Google/GitHub pair is set. |
| `Refusing to start: ... requires an https RADAR_PUBLIC_URL` | `RADAR_PUBLIC_URL` in `fly.toml` is missing or `http://`; set it to `https://<app>.fly.dev` or your domain. |
| `413 payload_too_large` "Request body too large" | A request body above 64 KB (uploads: `RADAR_MAX_UPLOAD_MB` + 64 KB). Expected for abuse; for big documents raise `RADAR_MAX_UPLOAD_MB`. |
| Every page says "Invalid host header" | You opened a host that is not `RADAR_PUBLIC_URL`'s; add it to `RADAR_ALLOWED_HOSTS` or fix `RADAR_PUBLIC_URL`. |
| Google "redirect_uri_mismatch" / GitHub "redirect_uri is not associated" | Callback URL differs from `RADAR_PUBLIC_URL` + `/api/auth/oauth/<provider>/callback`. |
| Sign-up says email + password is not available | SMTP is not configured on a public server (section 5); use Google/GitHub meanwhile. |
| Health checks fail right after the model download | The first check after a start loads the model; it passes on the next check. If it keeps failing, see `fly logs`. |
| Deploy fails while building the web app | Run `cd web && npm ci && npm run build` locally; the image runs exactly that. |

Building the image yourself (optional, needs Docker): `docker build -t ratiocination .`
