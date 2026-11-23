"""Seed demo.db with invented jobs and scores for the public demo (DEMO=1). No model calls, no real data.

    python -m jobfinder.demo [demo.db]
"""
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from . import db
from .eval import load_jobs

DEMO_DB = "demo.db"
RESUME_FILE = "sample-resume.pdf"            # shown in the side panel of the demo results; no such file exists
ROLES = ["AI Automation Engineer", "Solutions Engineer", "Implementation Engineer", "Workflow Developer"]   # invented
SAMPLES = Path(__file__).resolve().parent.parent / "samples"


def fake_score(job, expect, i):
    """A made-up score. Jobs the eval file marks as deal-breakers get one; the rest spread between 58 and 93."""
    blocked = expect.startswith("deal-breaker")
    score = 41 + i % 9 if blocked else 93 - (i * 7) % 36
    return {"fit_score": score,
            "matching_evidence": [] if score < 60 else ["Invented example: workflow automation in Python and webhooks",
                                                         "Invented example: calls an LLM API from Python"],
            "gaps": ["Invented example gap: no Kubernetes"] if i % 3 == 0 else [],
            "deal_breakers": [expect.replace("deal-breaker ", "Invented example: ")] if blocked else [],
            "summary": "Invented demo score. Nothing here comes from a real job or a real model."}


def seed(path=DEMO_DB):
    """Fill the database at `path` with the fake jobs if it has none yet. Returns the number of jobs scored."""
    con = db.connect(path)
    try:
        if db.scored_jobs(con):
            return 0
        raw = json.loads((SAMPLES / "eval_jobs.json").read_text(encoding="utf-8"))
        now = datetime.now(timezone.utc).isoformat()
        for i, (job, row) in enumerate(zip(load_jobs(), raw), 1):
            job.source = "demo"
            db.save(con, job, now, True, "demo data")
            db.set_score(con, job, fake_score(job, row.get("expect", ""), i))
        return len(raw)
    finally:
        con.close()


if __name__ == "__main__":
    out = sys.argv[1] if len(sys.argv) > 1 else DEMO_DB
    print(f"seeded {seed(out)} fake jobs into {out}")
