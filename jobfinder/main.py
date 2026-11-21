"""Daily run: fetch -> pre-filter -> score -> draft -> report. Never sends or applies."""
import argparse
import sys
from collections import Counter
from datetime import datetime, timezone

import yaml

from . import db, report
from .backends import get_backend
from .drafts import draft_note, eligible, write_draft
from .http import HttpClient
from .log import make_logger
from .prefilter import prefilter
from .scoring import load_profile, score_job
from .sources import ATS


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
    return jobs, returned


def run(argv=None, backend=None, http=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-model", action="store_true", help="fetch and pre-filter only; print counts")
    ap.add_argument("--db", default="jobs.db")
    args = ap.parse_args(argv)
    with open("config.yaml", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    with open("companies.yaml", encoding="utf-8") as f:
        companies = yaml.safe_load(f)["companies"]
    log = make_logger()
    http = http or HttpClient(cfg["user_agent"], cfg["request_delay_seconds"])
    con = db.connect(args.db)

    jobs, returned = collect(http, cfg, companies, log)
    passed_by_source, new_passed = Counter(), []
    now = datetime.now(timezone.utc)
    for job in jobs:
        if db.is_seen(con, job):
            continue
        ok, rule = prefilter(job, cfg, now)
        db.save(con, job, now.isoformat(), ok, rule)
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
    backend = backend or get_backend()
    for job in new_passed:
        score = score_job(backend, profile_text, job, log)
        if score is None:
            continue
        draft_path = None
        if eligible(score, cfg["score_threshold"]):
            note = draft_note(backend, profile_text, job, score, log)
            if note:
                draft_path = write_draft(job, note)
        db.set_score(con, job, score, draft_path)
        log("scored", job, fit_score=score["fit_score"], deal_breakers=score["deal_breakers"], draft=draft_path)
    rs = report.rows(con)
    report.write_xlsx(rs)
    report.write_dashboard(rs)
    print(f"scored {len(new_passed)} new jobs; report.xlsx and dashboard.html written")
    return 0


if __name__ == "__main__":
    sys.exit(run())
