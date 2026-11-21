"""Daily run: (import new resume) -> fetch -> pre-filter -> score -> draft + tailor resume -> report.
Never sends or applies. Extra commands: import-resume FILE, tailor JOB_URL."""
import argparse
import sys
from collections import Counter
from datetime import datetime, timezone

import yaml

from . import db, report, resume
from .backends import get_backend
from .drafts import draft_note, eligible, write_draft
from .http import HttpClient
from .log import make_logger
from .prefilter import prefilter
from .resume_docx import write_tailored
from .scoring import load_profile, score_job
from .dedupe import dedupe, norm_key
from .sources import AGGREGATORS, ATS
from .tailor import tailor_resume


def collect(http, cfg, companies, log):
    """Fetch every source. Returns (jobs, {source: count returned})."""
    jobs, returned = [], Counter()
    for c in companies:
        try:
            got = ATS[c["source"]](http, c)
        except Exception as e:  # noqa: BLE001
            log("fetch_failed", None, source=c["source"], board=c["board"], error=str(e)[:300])
            continue
        returned[c["source"]] += len(got)
        jobs += got
    for name in cfg.get("aggregators", []):
        try:
            got = AGGREGATORS[name](http, cfg, log)
        except Exception as e:  # noqa: BLE001
            log("fetch_failed", None, source=name, error=str(e)[:300])
            continue
        returned[name] += len(got)
        jobs += got
    return jobs, returned


def run(argv=None, backend=None, http=None):
    argv = sys.argv[1:] if argv is None else list(argv)
    if argv and argv[0] in COMMANDS:
        return COMMANDS[argv[0]](argv[1:], backend)
    ap = argparse.ArgumentParser(epilog="other commands: import-resume FILE, tailor JOB_URL")
    ap.add_argument("--no-model", action="store_true", help="fetch and pre-filter only; print counts")
    ap.add_argument("--db", default="jobs.db")
    args = ap.parse_args(argv)
    with open("config.yaml", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    with open("companies.yaml", encoding="utf-8") as f:
        companies = yaml.safe_load(f)["companies"]
    log = make_logger()

    def backend_once():
        nonlocal backend
        backend = backend or get_backend()
        return backend

    if not args.no_model:
        resume.import_if_new(backend_once, log)          # a new file in resume/ is read before the search
    res = resume.load_resume()
    cfg = resume.with_resume_keywords(cfg, res)
    if res:
        log("keywords", None, keywords=cfg["keywords"])
    http = http or HttpClient(cfg["user_agent"], cfg["request_delay_seconds"])
    con = db.connect(args.db)

    jobs, returned = collect(http, cfg, companies, log)
    passed_by_source, new_passed = Counter(), []
    now = datetime.now(timezone.utc)
    before = len(jobs)
    jobs = dedupe(jobs)
    log("dedupe", None, before=before, after=len(jobs))
    for job in jobs:
        if db.is_seen(con, job):
            continue
        if db.merge_duplicate(con, job, norm_key(job)):     # same company+title already stored from another source
            log("duplicate_of_stored", job)
            continue
        ok, rule = prefilter(job, cfg, now)
        db.save(con, job, now.isoformat(), ok, rule, norm_key(job))
        log("prefilter_pass" if ok else "prefilter_reject", job, reason=rule)
        if ok:
            passed_by_source[job.source] += 1
            new_passed.append(job)

    print(f"{'source':<12}{'returned':>9}{'passed':>8}")
    for s in returned:
        print(f"{s:<12}{returned[s]:>9}{passed_by_source[s]:>8}")
    if args.no_model:
        return 0

    profile_text = load_profile()
    backend = backend_once()
    tailored = 0
    for job in new_passed:
        score = score_job(backend, profile_text, job, log)
        if score is None:
            continue
        draft_path = resume_path = None
        if eligible(score, cfg["score_threshold"]):
            note = draft_note(backend, profile_text, job, score, log)
            if note:
                draft_path = write_draft(job, note)
            if res and cfg.get("tailor_resume", True):
                got = tailor_resume(backend, res, job, log)
                if got:
                    resume_path = write_tailored(*got, job)
                    tailored += 1
        db.set_score(con, job, score, draft_path, resume_path)
        log("scored", job, fit_score=score["fit_score"], deal_breakers=score["deal_breakers"], draft=draft_path,
            resume=resume_path)
    rs = report.rows(con)
    report.write_xlsx(rs)
    report.write_dashboard(rs)
    print(f"scored {len(new_passed)} new jobs, tailored {tailored} resumes; report.xlsx and dashboard.html written")
    return 0


def import_resume_cmd(argv, backend=None):
    ap = argparse.ArgumentParser(prog="python -m jobfinder import-resume",
                                 description="Read your resume and save the verified facts to resume.json.")
    ap.add_argument("file", help="PDF, DOCX, TXT or MD")
    args = ap.parse_args(argv)
    try:
        resume.read_resume(args.file)                 # fail on a bad file before any model call
    except (OSError, ValueError) as e:
        print(f"could not read {args.file}: {e}")
        return 1
    got = resume.import_resume(backend or get_backend(), args.file, make_logger())
    if not got:
        print("resume import failed; see logs/decisions.jsonl")
        return 1
    facts, dropped = got
    print(f"saved resume.json: {len(facts['roles'])} roles, {len(facts['skills'])} skills")
    print("search keywords:", ", ".join(facts["search_keywords"]) or "none")
    if dropped:
        print(f"{len(dropped)} items were not found word for word in your resume, so they were left out:")
        for x in dropped:
            print("  -", x)
    return 0


def tailor_cmd(argv, backend=None):
    ap = argparse.ArgumentParser(prog="python -m jobfinder tailor",
                                 description="Tailor your resume to one job that is already in jobs.db.")
    ap.add_argument("url", help="the job link from report.xlsx or dashboard.html")
    ap.add_argument("--db", default="jobs.db")
    args = ap.parse_args(argv)
    res = resume.load_resume()
    if not res:
        print("no resume.json yet. Run: python -m jobfinder import-resume <file>")
        return 1
    con = db.connect(args.db)
    job = db.find_job(con, args.url)
    if not job:
        print("that link is not in jobs.db. Run python -m jobfinder first.")
        return 1
    got = tailor_resume(backend or get_backend(), res, job, make_logger())
    if not got:
        print("tailoring failed; see logs/decisions.jsonl")
        return 1
    path = write_tailored(*got, job)
    db.set_resume(con, job, path)
    print(f"wrote {path} and its .changes.md. Read both before you send anything.")
    return 0


COMMANDS = {"import-resume": import_resume_cmd, "tailor": tailor_cmd}


if __name__ == "__main__":
    sys.exit(run())
