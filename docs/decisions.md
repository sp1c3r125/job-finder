# Decisions

Recorded decisions. Do not reopen without new evidence.

## Rules that stay
- The service never sends anything and never applies anywhere. The user reviews and sends by hand.
- Plain-code checks run before and after the model. The model uses only resume/profile facts and names gaps.
- Invalid model output is retried once, never repaired, and logged.
- The public repo has fake data only. Never commit profile.yaml, .env, the database, the real resume or generated outputs.
- No scraping of LinkedIn, Wellfound, YC Work at a Startup or anything behind a login. Public APIs and feeds only.
- Every decision is one line in `logs/decisions.jsonl` (a runtime log, not this file).

## Online service (decided 2026-11-21)
- Audience: only the owner. No other users for now.
- Access: through a private tunnel to 127.0.0.1. The app binds 127.0.0.1 and has no public login page.
- All SQL stays in `jobfinder/db.py`.
- Stack direction: FastAPI + Jinja2, one CSS file, little JS. No Supabase until M4.
- Schema: split a shared jobs catalogue from per-job scores, so adding users later is not a rewrite.
- Cost: a daily call cap and a kill switch wrap every model call (M2).

## Milestones
- M0 tooling and baseline. M1 read-only dashboard. M2 actions and guards.
- M3 public demo link: fake data only.
- M4 multi-user: TRIGGER. Build it when 5 or more job seekers ask, or when the owner decides to sell it.
  Needs sign-in, row-level security, consent, deletion, retention, per-user caps and legal review
  (Philippine Data Privacy Act).
- M5 public job digest: TRIGGER. See "M5 public job digest" below. M6 full site: see the end of that section.
- API backend for scheduled runs: TRIGGER. Batch API (50% cheaper), a cheaper model for scoring, and the web search tool
  for research ($10 per 1,000 searches plus tokens). Trigger: plan limits get in the way, or the job finder moves to a server.

## Source terms
- Himalayas: visible link to Himalayas, `applicationLink` unchanged, expired jobs removed, no passing of jobs to other boards.
- Remote OK: link back to the listing and name Remote OK. No logo.
- Remote.co is not a source. It has no RSS feed or API, and its Terms of Service (remote.co/tos, sections 8(d) and 9(f)) ban bots,
  scraping and aggregation without written consent. Check it by hand or with its email alerts.
- We Work Remotely: the RSS feed is used for personal use only, with links back to WWR, as its RSS page asks. WWR's API terms forbid
  "a job advertising or job search service" and storing its data, so turn WWR off before M4 unless WWR gives written permission.

## Open
- Browser baseline: Baseline Widely available (set in CLAUDE.md).
- modern-web-guidance is installed in .agents/skills/ (76f9136). CLAUDE.md says how to run it.

## M1 read-only dashboard
- FastAPI + Jinja2 + one CSS file, no JavaScript. Packages: fastapi, uvicorn, python-multipart (uploads, M2), jinja2; httpx is test-only.
- Host allow-list (TrustedHostMiddleware) from `allowed_hosts` in config.yaml; `/files/` serves only `drafts/` and `resumes/`.
- All SQL stays in `jobfinder/db.py` (enforced by tests/test_db.py).

## M2 actions and guards
- Actions: run now, resume upload, tailor one job. One background thread at a time (`runner.py`); a second request gets "busy".
- `guard.CappedBackend` wraps every default backend (web and CLI): kill switch (settings table or a `KILL` file) and `daily_call_cap`
  checked before each call; usage rows in the DB plus `model_call` / `model_refused` lines in the log. A refusal stops the run.
- POST forms: CSRF token, Origin checked against `allowed_hosts`. Uploads: 5 MB, PDF/DOCX/TXT/MD, magic-byte check, random name in `resume/`.
- Eval set: `python -m jobfinder.eval` (fake resume + 20 fake jobs). Not yet run against a live model.

## M3 public demo
- `DEMO=1` serves only invented data from `demo.db`; write routes answer 405; no downloads, no resume, no model, footer reworded.
- Exception: POST /theme works in the demo. It sets a theme cookie only, stores nothing and logs nothing.
- Real data stays private. Source terms: see "Source terms" above, including the We Work Remotely entry (bf85b32).

## M4 multi-user
- Not built. TRIGGER: 5 or more job seekers ask, or the owner decides to sell it.

## M5 public job digest (TRIGGER)
- Trigger: build only when I decide to test demand for a public list of remote AI-automation jobs open to the Philippines. The job finder must have run daily for at least 2 weeks first.
- Scope: `python -m jobfinder digest --days 7`, with no model calls, writes one weekly page on a site I operate. One LinkedIn company-page post a week links to that page.
- Content: jobs that passed the pre-filter, newest first, at most 20. Each item shows title, company, location, posted date, a link and "via <source>". No job description text. No fit scores or deal-breakers, because they are personal.
- Source rules (checked 2026-11-23):
  - Himalayas: allowed only on sites I operate. Link to Himalayas as the source, keep applicationLink unchanged, remove expired jobs. Never to other job boards, aggregators, Google Jobs or LinkedIn Jobs.
  - Remote OK: link back to the listing and name Remote OK. No logo.
  - We Work Remotely: leave out. Its API terms forbid a job search service.
  - Remote.co: leave out. Its terms ban bots and aggregation.
  - Greenhouse, Lever, Ashby and research results: link to the employer's own posting only.
  - No JobPosting structured data, no RSS and no feed to other sites.
  - LinkedIn: normal posts that link to my site only. Never LinkedIn job posts; posting jobs for another company needs a recruiting contract with it.
- Next trigger (M6, full site with employer posts or resume upload for other users): 200 subscribers, or 3 employers asking to post a job. Before M6, read each source's full terms (for Himalayas: himalayas.app/terms#api, which does not say whether commercial use is allowed) and get legal review for the Philippine Data Privacy Act.

## Fewer model calls (decided 2026-11-23)
- Code first, model last. A rule score (plain code, 0-100, reasons saved) is computed for every job that passes the pre-filter.
  The model reads only jobs with rule score >= `rule_min` (40), at most `model_daily_jobs` (15) a day, best rule score first.
  Other jobs show only "rule score" on the dashboard.
- Years rule: "N+ years" near AI, ML, LLM, automation, agents, software or engineering is compared with `years_ai_automation`; any other
  with `years_total` (both numbers in profile.yaml).
- Cover notes are drafted on request only (button on the job page). Tailoring defaults to `tailor_mode: rank` (reorder by word overlap,
  no model call, no rewording); `rewrite` keeps the model call. Research runs every `research_every_days` (7).
- Weights (title 40, skills 30, years 15, location 15) and `rule_min` were kept after calibration (`python -m jobfinder calibrate`).
  Only 2 saved jobs scored 70 or more, so tuning further would overfit.


## Research: blocked job boards
- JobStreet/JobsDB/SEEK: not a source. Terms 7(d) and 9(b) ban bots and scraping. The SEEK API is for posting jobs only. Revisit only with written consent from SEEK. A blocked domain stays blocked until its terms are checked.
- The list is BLOCKED_DOMAINS in jobfinder/sources/research.py (the only copy). Each entry matches the domain and every subdomain, not look-alikes.
  A blocked result is dropped before the link check, so no request goes to it. The count per run is logged as `research_blocked`.
  Redirects are followed by hand (5 hops at most); a hop to a blocked domain stops before the request is sent.

## UI layout fixes (2026-11-23)
- Baseline: Baseline Widely available (current Chrome, Edge, Firefox, Safari incl. iOS Safari). No JavaScript, no new dependencies, existing CSS tokens only.
  An external audit (axe-core, 390 and 1280 px, light and dark) found 0 accessibility violations; these are layout fixes.
- Guides used: `size-aware-styling`, `css-layout`, `form-factor-adaptation`, `html`, `search-hidden-content`, `responsive-table`, `coordinate-global-events` (read, not applied; see below).
  `legacy-scan` on `jobfinder/templates` and `jobfinder/static`: 0 findings before and after.
- Changed:
  1. Resume and Search profile `<dl>`: each panel is an inline-size container. Narrow: label above value. Wide (32rem or more): two columns, `minmax(0, 1fr)` for values.
     Values wrap with `overflow-wrap: break-word`, not `anywhere`, so ordinary words are no longer split.
  2. Resume, Search profile and Import a resume are in one `<details class="setup">`, closed, after the jobs table (Actions, then table, then details).
     Summary: file name, read date, skill count. The sub-headings are `h2`. The summary has no heading or link inside it, and keeps its default triangle.
  3. Matching evidence, Gaps and Deal-breakers are `<ul>` lists (`report.row_from` adds `*_list` fields; the joined strings stay for the xlsx and dashboard.html).
  4. The two job-page buttons share one `.actions` flex row with `gap` and `flex-wrap`. `flex-wrap: balance` is layered on; unsupported browsers ignore it and wrap normally.
  5. Rule-score pages hide Summary (it was a fixed placeholder sentence), Matching evidence, Gaps and Deal-breakers when empty. Model-scored pages keep all four with "None listed."
  6. Jobs table: "—" for no draft/resume (link when a file exists). The Location rule column is gone; the rule is on the job page.
  7. Times use `timezone:` from profile.yaml (UTC+8), in `<time datetime="...+08:00">`. A missing key or file means UTC. New module `jobfinder/localtime.py`.
     The resume read time shows as `2026-11-23 04:00 UTC+8`; the table shows posted dates only; the job page shows date and time.
- Skipped:
  - Temporal API (`coordinate-global-events`): it needs client JavaScript and is not Baseline (no Safari). Times are formatted on the server instead.
  - `details::details-content` and `name=` grouping: nothing to animate or exclude.
  - `hidden="until-found"`: `<details>` is preferred by the guide and Baseline.
  - Location rule stays in xlsx and dashboard.html (they are not the web UI).
- Follow-ups after the browser check (same day): the setup `<details>` has `open` when there is no resume.json, so the upload form shows on first use,
  and sits after Actions, above the jobs table. In the stacked table layout the wrapper drops `overflow` and `max-block-size`, so a phone page scrolls normally.
  A container query cannot style its own container, so `container-type` moved to a new `.table-container` div around `.table-wrapper`. Guides: `html`, `search-hidden-content`, `responsive-table`, `size-aware-styling`. `legacy-scan`: 0 findings.

## Run reliability, PR 1 (2026-11-23)
- Changed: `.gitattributes` (`text=auto`, `*.cmd` CRLF, `*.xml` untouched, binaries); a failed `claude -p` call now keeps the first 300 characters of
  stdout and stderr in the error (never the prompt); a failed runner task writes one `task_failed` line (label, error type, 300 characters) to
  `logs/decisions.jsonl`; GitHub Actions runs `pytest` on windows-latest, Python 3.12, on each push and PR; tests block non-loopback network connects;
  README says to put `BACKEND=claude-code` in `.env`, and no longer says the run drafts cover notes (on request only).
- Skipped: restoring "28 files with only line-ending changes": none differed from master (autocrlf hides CRLF; the index is already LF), and
  `git add --renormalize .` changed nothing. `get_backend()` still defaults to `api`; the default is unchanged on purpose (no behaviour change).
  `schedule/job-finder-task.xml` says UTF-16 but is ASCII; left alone.

## Deferred (proposed 2026-11-23)
Not built. Each waits for its trigger.
- ATS source review: after 14 daily runs, if Greenhouse, Lever and Ashby passed 0 jobs, cut `companies.yaml` to the boards that passed at least one job.
- Retention: when `jobs.db` is larger than 500 MB, or a run takes longer than 10 minutes, clear the description of rejected jobs older than 14 days (keep the row).
- Remove the `dashboard.html` export: when phone access works and the file has not been opened for 2 weeks.
- Microsoft Clarity: never on the private app or the demo. Only Mode A on a future public site (M5 digest or M6).

## Dark mode and theme button (PR #6, recorded 2026-11-23)
- Light, dark or device setting. The button is a form that posts to `POST /theme` (CSRF token and Origin checked like other POSTs).
  It sets a `theme` cookie (one year, `SameSite=Lax`, `HttpOnly`, `Secure` over https) or deletes it for "device". Nothing is stored
  in the database and nothing is logged. It also works in the demo (see M3).
- No JavaScript was added for it. The CSS follows `data-theme` and the device setting the same way.

## Phone access over Tailscale, PR 2 (2026-11-23)
- Changed: `ALLOWED_HOSTS_EXTRA` in `.env` (comma-separated) adds hostnames to the Host allow-list and the Origin check. Lowercased; empty items
  and wildcards are dropped. The demo ignores it. The app still binds 127.0.0.1; `tailscale serve --bg 8000` proxies the tailnet URL to it.
  The tailnet name stays in `.env`, never in the repo. README has the owner steps. CLAUDE.md now has the Git rules.
- Skipped: a logon scheduled task to start the dashboard (ask the owner first); `tailscale funnel` and any public exposure (never);
  installing Tailscale or running its commands (owner steps); a wildcard host (any `*` is ignored on purpose).

## M4 P0: mode switch and startup check (2026-11-23)
- Trigger met: the owner decided to offer the service to other job seekers. Plan: one codebase, two modes. `JOBFINDER_MODE=local|hosted`, default local.
  Local is unchanged. Hosted is an invite-only beta (up to 20 users, Philippines, free), built in milestones P0-P6.
- Added `jobfinder/settings.py`: the one `.env` loader (`backends.load_env` still imports from it), `mode()`, and `check_startup()`.
  Hosted mode refuses to start and lists every problem (names only, never values) unless `DATABASE_URL`, `SUPABASE_URL`, `SUPABASE_ANON_KEY`,
  `ANTHROPIC_API_KEY`, `SESSION_SECRET` and `ALLOWED_HOSTS_EXTRA` are set, `BACKEND` is `api` (claude-code is refused), `DEMO` is not 1,
  and `config.yaml` has `hosted_sources`. An unknown mode value is an error, not a fallback to local.
- Even with every setting present, `create_app` refuses to serve in hosted mode until sign-in exists (P3): the local app has no login.
- Changes to the plan found in the code: per-user job state moves to a `user_jobs` table (P1); research stays off in hosted v1;
  RLS uses its own non-bypass role, not the Supabase service role. See the approved plan.

## M4 P1: user_id in the schema (2026-11-23)
- `jobs` stays the shared catalogue. New `user_jobs(user_id, source, source_id, passed, rule, rule_score, rule_json)` holds what depends on one
  user's profile (the plan's change A: "one new column" was not enough, because `passed` and the rule score are per user).
  `job_scores`, `usage` and `settings` got `user_id` (primary keys now start with it). Local mode is user `local`.
- `db.connect(path, user_id="local")` returns a connection that carries the user id; every function in `db.py` reads and writes only that user's rows.
  No caller changed. `is_seen` and `merge_duplicate` stay catalogue-wide.
- Numbered migrations in `db.py` (not `.sql` files: all SQL stays in `db.py`), recorded in `schema_version`. Each runs in one transaction.
  Migration 1 is the old in-place fixes; migration 2 adds user_id. Before migration 2 runs on a database that has data, `<db>.bak-v2` is written once
  (SQLite backup API; `*.db.bak-*` is git-ignored).
- The old `jobs.passed/rule/rule_score/rule_json` columns are copied to `user_jobs` and left in place, unused. Nothing is dropped.
- Checked on a copy of the real jobs.db: the same 138 listed jobs before and after (hash of the rows equal), 5068 jobs and 136 scores kept.
- Test changes: 3 assertions in tests/test_research.py read `jobs.passed`/`rule`; they now join `user_jobs`. Expected values are unchanged.
  New: tests/test_db_users.py (migration on invented old DBs, backup, rollback, two-user separation).

## M4 P2: Postgres, row-level security, isolation test (2026-11-23)
- Hosted data lives in Postgres (Supabase later). `db.connect_pg(url, user_id)` returns a connection with the same `db.py` functions (`?` placeholders
  are translated; the one SQLite-only expression, `json_extract`, has a Postgres twin). Schema: `PG_MIGRATIONS` in `db.py`, `migrate_pg(admin_url)`
  is a deploy step run by the owner role. Columns match SQLite (tested), except the unused legacy `jobs` columns.
- Isolation: the app connects as `jobfinder_app`, the worker as `jobfinder_worker`. Both are NOBYPASSRLS, not superuser and not table owners.
  Every per-user table (`PER_USER_TABLES`: user_jobs, job_scores, usage, settings) has RLS enabled and forced, and one policy
  `user_id = current_setting('app.user_id')::uuid` for reads and writes. Each transaction starts with `set_config('app.user_id', id, true)`;
  a connection with no user set sees nothing. `user_id` is a plain uuid (no foreign key to `auth.users`), so CI runs on stock Postgres.
- The app role can only read the shared `jobs` catalogue; only the worker role writes it. Neither role can read `schema_version`.
- Tests (`tests/test_isolation.py`, run when `TEST_DATABASE_URL` is set): A cannot read, change, delete or insert B's rows, through the app
  functions and through the database role, in every per-user table; a structural test fails if any table with a `user_id` lacks RLS, FORCE or a policy;
  the isolation check itself is tested by breaking protection (drop the policy, open it to everyone, disable RLS) on each table: 12 cases, all caught.
- CI: new Linux job `isolation` with a `postgres:16` service container. The windows job is unchanged; these tests skip there.
- New dependency: `psycopg[binary]` (Postgres driver). Not used in local mode unless hosted code runs.
- Owner step: create the Supabase staging project (Singapore). Put the owner connection string in your secret store; the deploy step runs
  `migrate_pg` with it, then `set_pg_role_login` for the two roles. Turn off the Supabase Data API for the `public` schema (the app does not use it).

## M4 P3: sign-in, invites, consent, delete account, resume facts in the database (2026-11-23)
- `JOBFINDER_MODE=hosted` now serves a different app (`jobfinder/hosted.py`). The local app has no login and still refuses to run in hosted mode;
  the P0 "not available yet" stop is gone because `create_app` hands back the signed-in app.
- Sign-in is Supabase Auth over REST, no SDK (`jobfinder/auth.py`, httpx). Email link only, no password. Supabase is set not to create users;
  the app also checks its own `invites` table before sending a link and again when the link is used. Every address gets the same answer on the
  sign-in page, so it does not reveal who is invited. The link opens a page with a "Continue" button that does the verify step, because mail
  scanners open links and would use up a one-time link.
- The session cookie holds Supabase's own JWT (HttpOnly, Secure, SameSite=Lax). The JWT is verified here against Supabase's public keys
  (new dependency `PyJWT[crypto]`: signature, expiry, issuer, audience; ES256, RS256 and EdDSA only, never `none` or a shared secret).
  Session length is the JWT lifetime set in Supabase (owner setting; there is no refresh yet, so people sign in again when it ends).
- CSRF: one token per session (HMAC of the session cookie with `SESSION_SECRET`) plus the Origin check. A token from another session or from
  before sign-in is refused.
- `invites` is not per-user. The app role cannot read it; it can only call `is_invited(email)` (SECURITY DEFINER). The owner adds invites with SQL.
- New per-user tables in Postgres migration 2, all with RLS: `consents` (version and time), `resume_facts` (the checked facts and the resume text, as
  JSON text), `deletion_requests`. `job_scores.draft_text` holds hosted cover notes. All four are in `PER_USER_TABLES`, so the isolation tests cover them.
- Delete account: the user's request deletes every row of that user in every per-user table (one transaction, under RLS) and queues a
  `deletion_requests` row. That row stays so the worker, which holds a separate admin secret, can remove the Supabase login (`auth.delete_user`,
  `db.pending_deletions`, `db.finish_deletion`). The user request never calls the admin API. The worker loop itself is P4. Until it runs, the login
  exists but has no data and no invite use. Backups: how long deleted rows live in Supabase backups is a question for the lawyer.
- Files: hosted mode keeps none. Resume facts and cover notes are in the database. The tailored .docx is built in memory and streamed.
  An upload is written to a temporary file, read, and deleted (also when reading fails); its file name is not kept. There is no `/files` route.
- Model calls from the hosted app go through `guard.UserCappedBackend`: that user's own daily cap (`user_daily_call_cap`, default 20) and kill switch.
  Global cap, token counts and the worker queue are P4. Upload, tailor and note run inside the request for now (P4 moves them to the worker).
- Logs: hosted log lines hold the event, a 12-character hash of the user id, counts and short reasons only. Resume text, names, file names and
  model error messages are dropped (`make_hosted_log`). They go to standard output.
- Refactor with no change for local mode: `resume.import_resume` now calls `extract_facts` (read, extract, check; saves nothing) and writes the file itself.
  `resume_docx.write_docx` accepts a file-like object. `base.html` and `job.html` have `hosted` / `draft_text` branches that local never sets.
- UI follows modern-web-guidance (`autofill-sign-in-form`, `forms`): `type=email` with `autocomplete=email`, `required`, visible labels, no placeholder-only labels.
  Delete uses a typed confirmation field, not a dialog, so there is no script.
- Tests: `tests/test_hosted.py` (21, need `TEST_DATABASE_URL`, run in the Linux CI job): fake Supabase on an httpx mock transport; uninvited email
  refused; forged, expired, wrong-audience, wrong-issuer, HS256 and `none` tokens refused; missing, wrong and cross-session CSRF refused; foreign Origin
  refused; delete removes every row in every table found from the schema while another user's rows stay; upload file deleted; no `/files`; logs hold no personal text.
- Owner steps: in Supabase turn signups off, set the redirect URL to `https://<host>/auth/callback`, choose the JWT lifetime, and add invites:
  `INSERT INTO invites (email, invited_at) VALUES ('person@example.com', now()::text)` run as the owner. The privacy page and legal text are P5 and the lawyer.

## M4 P4: worker, per-user and global caps, run-now queue (2026-11-23)

- `python -m jobfinder.worker` is the only hosted process that fetches jobs and spends the owner's model budget on a schedule. A daily pass fetches
  the shared catalogue once (only companies on a `hosted_sources` source; aggregators are never fetched in hosted mode), then handles each active user
  on that user's own connection, so row-level security applies as in the web app. `--queue` serves only the users who pressed "Search now" and does not fetch.
- One worker at a time: a Postgres advisory lock (`db.try_advisory_lock`). A second worker exits with "another worker is running".
- Per user: pre-filter on the catalogue with their resume keywords and target (`user_jobs`), rule scores, then model scoring through `pipeline.model_gate`
  (the same selection local mode uses: `rule_min`, `model_daily_jobs`). Hosted users have no profile.yaml or target.yaml, so those files are never read.
- Limits, checked before every model call in this order: global kill switch, global daily cap (`global_daily_call_cap`, default 300), the user's kill switch,
  the user's daily cap (`user_daily_call_cap`, default 20). A global limit ends the whole pass; a user's own limit ends only that user.
  Global state is not per-user: `global_settings` (the owner sets `kill` with SQL) and the SECURITY DEFINER function `global_calls_today()` (a count, no rows).
  `active_users()` (worker role only) lists users who consented, uploaded a resume and have not asked to be deleted.
- `usage` records `model`, `tokens_in`, `tokens_out` (a `kind='tokens'` row after each API call, not counted as a call), so cost per user can be read from the table.
  Local mode has the columns too (SQLite migration 3) and leaves them empty.
- "Search now": `runs` (per-user, RLS) has one row per user per UTC day, so a second request is refused by the primary key. The web request only inserts the row;
  the worker does the search. The page shows queued, done, or already used.
- The worker also finishes account deletions (`sweep_deletions`, using `SUPABASE_SERVICE_KEY`, the only place the admin key is read). A failure leaves the request queued.
- Hosted mode refuses `BACKEND=claude-code` (P0 check, repeated in `worker.main`). The worker needs `WORKER_DATABASE_URL` (the `jobfinder_worker` role).
- Refactor with no change for local mode: the model-gate selection in `main.run` moved to `pipeline.model_gate`; `db.save` now calls `_insert_job` + `_set_user_job`.
  Two existing tests assert the schema version; they now expect the latest migration number (3) instead of a fixed 2. Their data checks are unchanged.
- Not done here: upload, tailor and note still run inside the web request (they are capped per user); a retry after a refused call logs two refusals; retention is P5.
- Tests: `tests/test_worker.py` (12) and one in `test_hosted.py`, in the Linux CI job: two invented users scored in one run, a user at the cap stops while the other
  continues, a user kill switch, the global kill switch, the global cap, one run-now a day, one worker at a time, the app role cannot read the queue or list users,
  deleted users are skipped and swept, only hosted sources are fetched, hosted refuses claude-code.
- Owner steps: create the Anthropic key with a spend limit; set `WORKER_DATABASE_URL`, `SUPABASE_SERVICE_KEY` in the host's secret store (worker only, never the web service);
  schedule the daily pass and a `--queue` pass every few minutes (P6 covers the host).
