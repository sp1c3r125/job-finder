"""Eval set: fake resume + 20 fake jobs through the real prompts. Run before relying on a new model or backend:

    python -m jobfinder.eval [--runs 2]

Uses BACKEND from .env, behind the daily cap and kill switch (about 60 model calls per run). Nothing is sent anywhere.
Pass: 0 unsupported lines in tailored resumes, 0 notes of 200 words or more, every score of 70 or more has evidence,
and under 10% of model calls fail validation (in each run)."""
import argparse
import json
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import yaml

from . import resume
from .checks import terms, unsupported
from .drafts import draft_note, eligible
from .guard import capped_default
from .log import make_logger
from .models import Job
from .scoring import score_job
from .tailor import tailor_resume

SAMPLES = Path(__file__).resolve().parent.parent / "samples"
FAILED = ("score_failed", "draft_failed", "tailor_failed", "resume_extract_failed")


def load_jobs(path=SAMPLES / "eval_jobs.json"):
    return [Job(**{k: v for k, v in j.items() if k != "expect"}) for j in json.loads(Path(path).read_text(encoding="utf-8"))]


def evaluate(backend, res, jobs, threshold, events):
    """One pass over the jobs. `events` is the list the logger appends (event, fields) to. Returns a metrics dict."""
    def log(event, job=None, **f):
        events.append((event, f))

    m = {"jobs": len(jobs), "scored": 0, "eligible": 0, "unsupported_lines": 0, "long_notes": 0, "no_evidence": 0}
    profile = yaml.safe_dump({"resume": resume.profile_facts(res)}, sort_keys=False, allow_unicode=True)
    for job in jobs:
        score = score_job(backend, profile, job, log)
        if not score:
            continue
        m["scored"] += 1
        if score["fit_score"] >= threshold and not score["matching_evidence"]:
            m["no_evidence"] += 1
        if not eligible(score, threshold):
            continue
        m["eligible"] += 1
        note = draft_note(backend, profile, job, score, log)
        if note and len(note.split()) >= 200:
            m["long_notes"] += 1
        got = tailor_resume(backend, res, job, log)
        if got:
            tailored, _ = got
            job_terms = sorted({t for t, strong in terms(f"{job.title}\n{job.description}") if strong})
            for role, original in zip(tailored["roles"], res["roles"]):
                for b in role["bullets"]:
                    if b not in original["bullets"] and unsupported(b, res["text"], res["text"], job_terms):
                        m["unsupported_lines"] += 1
    m["failed_calls"] = sum(1 for e, _ in events if e in FAILED)
    return m


def verdict(runs, calls_per_run):
    """List of (check, passed) for the pass rules."""
    out = [("0 unsupported lines in tailored resumes", all(r["unsupported_lines"] == 0 for r in runs)),
           ("0 notes of 200 words or more", all(r["long_notes"] == 0 for r in runs)),
           ("every score >= threshold has evidence", all(r["no_evidence"] == 0 for r in runs)),
           ("invalid-output rate under 10% in each run",
            all(r["failed_calls"] < 0.10 * max(calls_per_run[i], 1) for i, r in enumerate(runs)))]
    return out


def write_verdict(path, checks, threshold):
    """Append one line to the eval log: date, PASS or FAIL, score (checks passed of total) and the fit-score threshold."""
    passed = sum(1 for _, ok in checks if ok)
    rec = {"date": datetime.now(timezone.utc).date().isoformat(), "event": "eval_verdict",
           "verdict": "PASS" if passed == len(checks) else "FAIL", "score": f"{passed}/{len(checks)}",
           "threshold": threshold}
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec) + "\n")
    return rec


def main(argv=None, backend=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--runs", type=int, default=2)
    args = ap.parse_args(argv)
    with open("config.yaml", encoding="utf-8") as f:
        threshold = yaml.safe_load(f)["score_threshold"]
    jobs = load_jobs()
    log = make_logger("logs/eval.jsonl")
    backend = backend or capped_default("jobs.db", log)
    with tempfile.TemporaryDirectory() as tmp:
        res_path = Path(tmp) / "resume.json"
        got = resume.import_resume(backend, SAMPLES / "resume.sample.md", log, out=str(res_path))
        if not got:
            print("FAIL: could not import the sample resume; see logs/eval.jsonl")
            return 1
        res = resume.load_resume(str(res_path))
    runs, calls = [], []
    for i in range(args.runs):
        events = []
        runs.append(evaluate(backend, res, jobs, threshold, events))
        calls.append(len(jobs) + 2 * runs[-1]["eligible"])
        print(f"run {i + 1}: {runs[-1]}")
    ok = True
    checks = verdict(runs, calls)
    write_verdict("logs/eval.jsonl", checks, threshold)
    for name, passed in checks:
        print(("PASS  " if passed else "FAIL  ") + name)
        ok = ok and passed
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
