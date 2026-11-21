import json
import sqlite3

from .models import Job

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
  source TEXT, source_id TEXT, company TEXT, title TEXT, url TEXT, posted TEXT,
  location TEXT, remote INTEGER, description TEXT, also_seen_on TEXT DEFAULT '[]',
  first_seen TEXT, passed INTEGER, rule TEXT,
  score_json TEXT, draft_path TEXT, norm_key TEXT, resume_path TEXT,
  PRIMARY KEY (source, source_id)
)"""


def connect(path="jobs.db"):
    con = sqlite3.connect(path)
    con.execute(SCHEMA)
    if "resume_path" not in {r[1] for r in con.execute("PRAGMA table_info(jobs)")}:   # jobs.db made before resumes
        con.execute("ALTER TABLE jobs ADD COLUMN resume_path TEXT")
    return con


def is_seen(con, job):
    return con.execute("SELECT 1 FROM jobs WHERE source=? AND source_id=?", (job.source, job.source_id)).fetchone() is not None


def save(con, job, now_iso, passed, rule, key=""):
    con.execute(
        "INSERT INTO jobs (source,source_id,company,title,url,posted,location,remote,description,also_seen_on,first_seen,passed,rule,norm_key)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (job.source, job.source_id, job.company, job.title, job.url, job.posted, job.location,
         None if job.remote is None else int(job.remote), job.description, json.dumps(job.also_seen_on),
         now_iso, int(passed), rule, key),
    )
    con.commit()


def set_score(con, job, score, draft_path=None, resume_path=None):
    con.execute("UPDATE jobs SET score_json=?, draft_path=?, resume_path=? WHERE source=? AND source_id=?",
                (json.dumps(score), draft_path, resume_path, job.source, job.source_id))
    con.commit()


def set_resume(con, job, path):
    con.execute("UPDATE jobs SET resume_path=? WHERE source=? AND source_id=?", (path, job.source, job.source_id))
    con.commit()


def find_job(con, url):
    """The stored job with this link, or None."""
    row = con.execute("SELECT source,source_id,company,title,url,posted,location,remote,description "
                      "FROM jobs WHERE url=? LIMIT 1", (url,)).fetchone()
    if not row:
        return None
    return Job(*row[:7], None if row[7] is None else bool(row[7]), row[8])


def merge_duplicate(con, job, key):
    """If another source already stored this company+title, record this source on that row. True if merged."""
    row = con.execute("SELECT source, source_id, also_seen_on FROM jobs WHERE norm_key=? ORDER BY first_seen, posted LIMIT 1", (key,)).fetchone()
    if not row:
        return False
    source, source_id, also = row[0], row[1], json.loads(row[2] or "[]")
    if job.source == source:
        return False                       # same source: a separate posting, not a cross-source duplicate
    if job.source not in also:
        also.append(job.source)
        con.execute("UPDATE jobs SET also_seen_on=? WHERE source=? AND source_id=?", (json.dumps(also), source, source_id))
        con.commit()
    return True
