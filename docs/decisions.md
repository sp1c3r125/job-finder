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
- We Work Remotely: terms not verified on the official page (it returned 403 to automated fetches). Check by hand before M3.

## Open
- Browser baseline: Baseline Widely available (set in CLAUDE.md).
- The modern-web-guidance skill is not installed (`.agents/skills/` is missing).
