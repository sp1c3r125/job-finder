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
- `BLOCKED_DOMAINS` in `jobfinder/sources/research.py`: jobstreet.com, jobsdb.com, seek.com.au, seek.co.nz, linkedin.com, indeed.com, glassdoor.com, ziprecruiter.com, foundit.in, remote.co,
  and the two that were already blocked (wellfound.com, workatastartup.com). Each matches the domain and every subdomain, not look-alikes.
  A blocked result is dropped before the link check, so no request goes to it. The count per run is logged as `research_blocked`.
