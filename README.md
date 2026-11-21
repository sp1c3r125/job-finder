# job-finder

Every morning: fetch new remote AI/automation jobs, keep the ones open to the Philippines, score them
against your profile with Claude, and draft a short cover note for the best ones.
Optional: give it your resume. It then searches with keywords from the resume, scores against the resume facts,
and makes a tailored resume (.docx) for each job that gets a cover note.
**It never sends anything and never applies anywhere.** Drafts are files you read and send yourself.

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
pip install -r requirements.txt          # tests: pip install -r requirements-dev.txt
copy profile.example.yaml profile.yaml      # then put your real facts in profile.yaml
set BACKEND=api                              # or claude-code; for api put ANTHROPIC_API_KEY in .env
python -m jobfinder                          # full run
python -m jobfinder --no-model               # fetch + pre-filter only, prints counts
pytest
```
Outputs (all git-ignored): `jobs.db`, `report.xlsx`, `dashboard.html`, `drafts/`, `resumes/`, `logs/decisions.jsonl`.
Schedule: see `schedule/job-finder-task.xml` and `schedule/run_daily.cmd` (edit paths and BACKEND).

## Resume
```
python -m jobfinder import-resume resume/my-resume.pdf    # or put the file in resume/; the next run reads it
python -m jobfinder tailor <job link from the report>    # tailor for one stored job, any score
```
1. **Import** (PDF, DOCX, TXT or MD). Claude copies the facts out of the resume. Plain code then keeps only the
   items it finds in the resume text, word for word or nearly so (PDF noise). It lists what it left out.
   The result is `resume.json` (git-ignored). A scanned PDF has no text layer: use a text PDF or DOCX.
2. **Search.** Claude also suggests 3-8 search keywords from the resume. Up to `max_resume_keywords` of them
   are added to `keywords` for the pre-filter and the Himalayas search. Scoring uses `profile.yaml` (optional:
   your location, limits, known gaps) plus the resume facts.
3. **Tailor.** For each job that gets a cover note, Claude selects, orders and rephrases resume lines for the job.
   Code, not the model, writes names, titles, employers, dates and education. Code rejects a rewritten line that
   adds a number, a tool-like term (AWS, C#, n8n, LangGraph) or a strong job term that its source line lacks,
   and keeps the original line. Skills must come from the resume. Job needs the resume lacks go in "Gaps".
   Output: `resumes/<company>__<title>__<source>.docx` and a `.changes.md` with gaps, changes and reverts.
   The dashboard links both files. You read and send them yourself.

## Design decisions
- Pre-filter is plain code (age, remote, location, keywords from `config.yaml`) so the model only sees plausible jobs.
- Backends: `BACKEND=api` (Anthropic SDK, forced tool call for JSON) or `BACKEND=claude-code`
  (`claude -p --output-format json --json-schema ... --tools "" --no-session-persistence`, job text on stdin).
- Invalid model output is retried once, never repaired, and the failure is logged.
- The model may use only `profile.yaml` facts; anything the job needs that the profile lacks is a gap.
- Drafts only for score >= 70 with no deal-breakers, under 200 words.
- Every decision is one line in `logs/decisions.jsonl`.
- Resume facts are checked by plain code after the model copies them; a tailored line that fails a check falls
  back to the original wording. The real resume and everything made from it are git-ignored.

## Known limits
- Posted date: Greenhouse uses `first_published` (falls back to `updated_at`); only jobs from the last 3 days pass.
- Greenhouse has no remote flag, so remote is inferred from the text ("remote" in location or description).
- Location matching is keyword based and strict; some valid jobs will be missed.
- Only the companies listed in `companies.yaml` are checked.
- Himalayas `pubDate` means last updated, so an old job that was edited can look new.
- Remote OK and WWR give free-text locations; only clear allowed terms pass, so some valid jobs are missed.
- Same-source repeat titles (one role in several locations) are kept separate; only cross-source matches are merged.
- The score is a model opinion; read the evidence and gaps before relying on it.
- The tailoring checks catch added numbers and tool names, not every change in meaning. A plain word such as
  "led" or "owned" can still overstate a line. Read the tailored resume before you send it.
- A strong job term written in lower case is caught; a plain word from the job (for example "python") is not.

## Private dashboard (web)

A read-only page of your scored jobs, best match first. It never sends or applies anywhere.

```
python -m uvicorn jobfinder.web:app --host 127.0.0.1
```

Open http://127.0.0.1:8000. Keep `--host 127.0.0.1`: the app has no login, so reach it from your phone only through a
private tunnel that requires you to sign in. Add the tunnel's hostname to `allowed_hosts` in `config.yaml`
(default: 127.0.0.1 and localhost); any other Host header gets a 400. Downloads are limited to `drafts/` and `resumes/`.
