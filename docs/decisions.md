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

## Source terms
- Himalayas: visible link to Himalayas, `applicationLink` unchanged, expired jobs removed, no passing of jobs to other boards.
- Remote OK: link back to the listing and name Remote OK. No logo.
- Remote.co is not a source. It has no RSS feed or API, and its Terms of Service (remote.co/tos, sections 8(d) and 9(f)) ban bots,
  scraping and aggregation without written consent. Check it by hand or with its email alerts.
- We Work Remotely: the RSS feed is used for personal use only, with links back to WWR, as its RSS page asks. WWR's API terms forbid
  "a job advertising or job search service" and storing its data, so turn WWR off before M4 unless WWR gives written permission.

## Open
- Browser baseline: Baseline Widely available (set in CLAUDE.md).
- The modern-web-guidance skill is not installed (`.agents/skills/` is missing).

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
- Real data stays private. Source terms (Himalayas, Remote OK, We Work Remotely) must be verified by the owner before any real data is public.

## M4 multi-user
- Not built. TRIGGER: 5 or more job seekers ask, or the owner decides to sell it.
