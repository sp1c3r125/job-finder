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
