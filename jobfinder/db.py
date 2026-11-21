"""All SQL lives here. Nothing else in the project may import sqlite3 or write SQL."""
import json
import sqlite3
from datetime import datetime, timezone

from .models import Job

# `jobs` is the shared catalogue. `job_scores` holds what the model said about a job for the user
# (score, draft, tailored resume). Split now so adding users later means one new column, not a rewrite.
SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
  source TEXT, source_id TEXT, company TEXT, title TEXT, url TEXT, posted TEXT,
  location TEXT, remote INTEGER, description TEXT, also_seen_on TEXT DEFAULT '[]',
  first_seen TEXT, passed INTEGER, rule TEXT, norm_key TEXT, expires TEXT DEFAULT '',
  PRIMARY KEY (source, source_id)
);
CREATE TABLE IF NOT EXISTS job_scores (
  source TEXT, source_id TEXT, score_json TEXT, draft_path TEXT, resume_path TEXT,
  PRIMARY KEY (source, source_id)
);
CREATE TABLE IF NOT EXISTS usage (day TEXT, ts TEXT, kind TEXT, note TEXT);
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT);
"""


def connect(path="jobs.db"):
    con = sqlite3.connect(path)
    con.executescript(SCHEMA)
    cols = {r[1] for r in con.execute("PRAGMA table_info(jobs)")}
    if "expires" not in cols:                                   # jobs.db made before expiry was stored
        con.execute("ALTER TABLE jobs ADD COLUMN expires TEXT DEFAULT ''")
    if {"score_json", "draft_path", "resume_path"} <= cols:     # jobs.db made before the split: copy once
        con.execute("INSERT OR IGNORE INTO job_scores SELECT source, source_id, score_json, draft_path, resume_path "
                    "FROM jobs WHERE score_json IS NOT NULL OR resume_path IS NOT NULL")
    con.commit()
    return con


def is_seen(con, job):
    return con.execute("SELECT 1 FROM jobs WHERE source=? AND source_id=?", (job.source, job.source_id)).fetchone() is not None


def save(con, job, now_iso, passed, rule, key=""):
    con.execute(
        "INSERT INTO jobs (source,source_id,company,title,url,posted,location,remote,description,also_seen_on,"
        "first_seen,passed,rule,norm_key,expires) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (job.source, job.source_id, job.company, job.title, job.url, job.posted, job.location,
         None if job.remote is None else int(job.remote), job.description, json.dumps(job.also_seen_on),
         now_iso, int(passed), rule, key, job.expires),
    )
    con.commit()


def set_score(con, job, score, draft_path=None, resume_path=None):
    con.execute("INSERT INTO job_scores (source,source_id,score_json,draft_path,resume_path) VALUES (?,?,?,?,?) "
                "ON CONFLICT(source,source_id) DO UPDATE SET score_json=excluded.score_json, "
                "draft_path=excluded.draft_path, resume_path=excluded.resume_path",
                (job.source, job.source_id, json.dumps(score), draft_path, resume_path))
    con.commit()


def set_resume(con, job, path):
    con.execute("INSERT INTO job_scores (source,source_id,resume_path) VALUES (?,?,?) "
                "ON CONFLICT(source,source_id) DO UPDATE SET resume_path=excluded.resume_path",
                (job.source, job.source_id, path))
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


_SCORED = ("SELECT j.source,j.source_id,j.company,j.title,j.url,j.posted,j.rule,s.score_json,s.draft_path,"
           "j.also_seen_on,s.resume_path,j.location,j.expires "
           "FROM jobs j JOIN job_scores s ON s.source=j.source AND s.source_id=j.source_id "
           "WHERE s.score_json IS NOT NULL AND (j.expires IS NULL OR j.expires='' OR j.expires>?)")


def scored_jobs(con, now_iso=None):
    """Raw rows for every scored job that has not expired, as dicts. Expired jobs (Himalayas expiryDate) are left out."""
    now_iso = now_iso or datetime.now(timezone.utc).isoformat()
    cur = con.execute(_SCORED, (now_iso,))
    names = [d[0] for d in cur.description]
    return [dict(zip(names, row)) for row in cur.fetchall()]


def get_scored_job(con, source, source_id, now_iso=None):
    now_iso = now_iso or datetime.now(timezone.utc).isoformat()
    cur = con.execute(_SCORED + " AND j.source=? AND j.source_id=?", (now_iso, source, source_id))
    names = [d[0] for d in cur.description]
    row = cur.fetchone()
    if not row:
        return None
    out = dict(zip(names, row))
    desc = con.execute("SELECT description FROM jobs WHERE source=? AND source_id=?", (source, source_id)).fetchone()
    out["description"] = desc[0] if desc else ""
    return out


def get_job(con, source, source_id):
    """The stored job with this key, or None."""
    row = con.execute("SELECT source,source_id,company,title,url,posted,location,remote,description "
                      "FROM jobs WHERE source=? AND source_id=?", (source, source_id)).fetchone()
    if not row:
        return None
    return Job(*row[:7], None if row[7] is None else bool(row[7]), row[8])


def add_usage(con, kind, note=""):
    """One row per model call ('call') or refused call ('refused')."""
    now = datetime.now(timezone.utc)
    con.execute("INSERT INTO usage (day, ts, kind, note) VALUES (?,?,?,?)", (now.date().isoformat(), now.isoformat(), kind, note))
    con.commit()


def calls_today(con):
    day = datetime.now(timezone.utc).date().isoformat()
    return con.execute("SELECT COUNT(*) FROM usage WHERE day=? AND kind='call'", (day,)).fetchone()[0]


def get_setting(con, key, default=""):
    row = con.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return row[0] if row else default


def set_setting(con, key, value):
    con.execute("INSERT INTO settings (key,value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))
    con.commit()
