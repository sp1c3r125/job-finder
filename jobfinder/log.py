import json
from datetime import datetime, timezone
from pathlib import Path


def make_logger(path="logs/decisions.jsonl"):
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)

    def log(event, job=None, **fields):
        rec = {"ts": datetime.now(timezone.utc).isoformat(), "event": event}
        if job is not None:
            rec.update(source=job.source, source_id=job.source_id, company=job.company, title=job.title)
        rec.update(fields)
        with p.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return log
