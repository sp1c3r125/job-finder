# job-finder

Every morning: fetch new remote AI/automation jobs, keep the ones open to the Philippines, score them
against your profile with Claude, and draft a short cover note for the best ones.
**It never sends anything and never applies anywhere.** Drafts are text files you read and send yourself.

## Sources
Public job-board JSON APIs only: Greenhouse, Lever, Ashby. Companies and board names are in `companies.yaml`.
Three aggregators (enabled in `config.yaml` under `aggregators`), all using their official public feeds:
- **Himalayas** `/jobs/api/search`: keyword search, newest first, `country=PH`, `worldwide=true`, `timezone=UTC+8`.
  429 responses back off using `Retry-After`. Personal use only: the data is not republished. Credit shown in the dashboard.
- **Remote OK** `https://remoteok.com/api`: the first element (terms) is skipped and logged. The dashboard shows
  "via Remote OK" linking to the listing, as its terms ask. The feed is about 24h delayed and many listings are
  not truly remote, so the location rule is strict (a blank or "US Only" location is rejected).
- **We Work Remotely** RSS: programming and all-other-remote category feeds.
Duplicates across sources (same company + title) keep the earliest posted record; the other sources are listed
in "also seen on". The report and dashboard have a Source column.
Why only public APIs: they are published for this use, need no login, and have stable formats.
No scraping of LinkedIn, Wellfound, YC Work at a Startup or anything behind a login.
Requests send a user agent, wait 1 second between calls, back off on HTTP 429, and are cached for the day in `.cache/`.

## Run
```
pip install -r requirements.txt
copy profile.example.yaml profile.yaml      # then put your real facts in profile.yaml
set BACKEND=api                              # or claude-code; for api put ANTHROPIC_API_KEY in .env
python -m jobfinder                          # full run
python -m jobfinder --no-model               # fetch + pre-filter only, prints counts
pytest
```
Outputs (all git-ignored): `jobs.db`, `report.xlsx`, `dashboard.html`, `drafts/`, `logs/decisions.jsonl`.
Schedule: see `schedule/job-finder-task.xml` and `schedule/run_daily.cmd` (edit paths and BACKEND).

## Design decisions
- Pre-filter is plain code (age, remote, location, keywords from `config.yaml`) so the model only sees plausible jobs.
- Backends: `BACKEND=api` (Anthropic SDK, forced tool call for JSON) or `BACKEND=claude-code`
  (`claude -p --output-format json --json-schema ... --tools "" --no-session-persistence`, job text on stdin).
- Invalid model output is retried once, never repaired, and the failure is logged.
- The model may use only `profile.yaml` facts; anything the job needs that the profile lacks is a gap.
- Drafts only for score >= 70 with no deal-breakers, under 200 words.
- Every decision is one line in `logs/decisions.jsonl`.

## Known limits
- Posted date: Greenhouse uses `first_published` (falls back to `updated_at`); only jobs from the last 3 days pass.
- Greenhouse has no remote flag, so remote is inferred from the text ("remote" in location or description).
- Location matching is keyword based and strict; some valid jobs will be missed.
- Only the companies listed in `companies.yaml` are checked.
- Himalayas `pubDate` means last updated, so an old job that was edited can look new.
- Remote OK and WWR give free-text locations; only clear allowed terms pass, so some valid jobs are missed.
- Same-source repeat titles (one role in several locations) are kept separate; only cross-source matches are merged.
- The score is a model opinion; read the evidence and gaps before relying on it.
