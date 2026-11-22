"""All SQL lives here. Nothing else in the project may import sqlite3 or write SQL."""
import json
import sqlite3
from datetime import datetime, timedelta, timezone

from .models import Job

# `jobs` is the shared catalogue. `job_scores` holds what the model said about a job for the user
# (score, draft, tailored resume). Split now so adding users later means one new column, not a rewrite.
SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
  source TEXT, source_id TEXT, company TEXT, title TEXT, url TEXT, posted TEXT,
  location TEXT, remote INTEGER, description TEXT, also_seen_on TEXT DEFAULT '[]',
  first_seen TEXT, passed INTEGER, rule TEXT, norm_key TEXT, expires TEXT DEFAULT '', undated INTEGER DEFAULT 0,
  rule_score INTEGER, rule_json TEXT,
  PRIMARY KEY (source, source_id)
);
CREATE TABLE IF NOT EXISTS job_scores (
  source TEXT, source_id TEXT, score_json TEXT, draft_path TEXT, resume_path TEXT, scored_at TEXT,
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
    if "undated" not in cols:                                   # jobs.db made before undated research results
        con.execute("ALTER TABLE jobs ADD COLUMN undated INTEGER DEFAULT 0")
    if "rule_score" not in cols:                                # jobs.db made before the rule score
        con.execute("ALTER TABLE jobs ADD COLUMN rule_score INTEGER")
        con.execute("ALTER TABLE jobs ADD COLUMN rule_json TEXT")
    if "scored_at" not in {r[1] for r in con.execute("PRAGMA table_info(job_scores)")}:
        con.execute("ALTER TABLE job_scores ADD COLUMN scored_at TEXT")
    if {"score_json", "draft_path", "resume_path"} <= cols:     # jobs.db made before the split: copy once
        con.execute("INSERT OR IGNORE INTO job_scores (source,source_id,score_json,draft_path,resume_path) SELECT source, source_id, score_json, draft_path, resume_path "
                    "FROM jobs WHERE score_json IS NOT NULL OR resume_path IS NOT NULL")
    con.commit()
    return con


def is_seen(con, job):
    return con.execute("SELECT 1 FROM jobs WHERE source=? AND source_id=?", (job.source, job.source_id)).fetchone() is not None


def save(con, job, now_iso, passed, rule, key=""):
    con.execute(
        "INSERT INTO jobs (source,source_id,company,title,url,posted,location,remote,description,also_seen_on,"
        "first_seen,passed,rule,norm_key,expires,undated) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (job.source, job.source_id, job.company, job.title, job.url, job.posted, job.location,
         None if job.remote is None else int(job.remote), job.description, json.dumps(job.also_seen_on),
         now_iso, int(passed), rule, key, job.expires, int(job.undated)),
    )
    con.commit()


def requalify(con, job, posted, undated, passed, rule):
    """Re-decide one stored job: new posted date, undated flag and pre-filter result."""
    con.execute("UPDATE jobs SET posted=?, undated=?, passed=?, rule=? WHERE source=? AND source_id=?",
                (posted, int(undated), int(passed), rule, job.source, job.source_id))
    con.commit()


def set_score(con, job, score, draft_path=None, resume_path=None):
    con.execute("INSERT INTO job_scores (source,source_id,score_json,draft_path,resume_path,scored_at) VALUES (?,?,?,?,?,?) "
                "ON CONFLICT(source,source_id) DO UPDATE SET score_json=excluded.score_json, "
                "draft_path=excluded.draft_path, resume_path=excluded.resume_path, scored_at=excluded.scored_at",
                (job.source, job.source_id, json.dumps(score), draft_path, resume_path,
                 datetime.now(timezone.utc).isoformat()))
    con.commit()


def set_draft(con, job, path):
    con.execute("INSERT INTO job_scores (source,source_id,draft_path) VALUES (?,?,?) "
                "ON CONFLICT(source,source_id) DO UPDATE SET draft_path=excluded.draft_path",
                (job.source, job.source_id, path))
    con.commit()


def model_scored_today(con):
    """How many jobs the model scored today (UTC day)."""
    day = datetime.now(timezone.utc).date().isoformat()
    return con.execute("SELECT COUNT(*) FROM job_scores WHERE score_json IS NOT NULL AND substr(scored_at,1,10)=?",
                       (day,)).fetchone()[0]


def passed_jobs(con):
    """Every stored job that passed the pre-filter, as Jobs (rule_score filled in when stored)."""
    out = []
    for r in con.execute("SELECT source,source_id,company,title,url,posted,location,remote,description,expires,rule_score "
                         "FROM jobs WHERE passed=1").fetchall():
        out.append(Job(*r[:7], None if r[7] is None else bool(r[7]), r[8], expires=r[9] or "", rule_score=r[10]))
    return out


def set_rule_scores(con, items):
    """items: [(job, rule_score_dict)]. One transaction."""
    con.executemany("UPDATE jobs SET rule_score=?, rule_json=? WHERE source=? AND source_id=?",
                    [(r["score"], json.dumps(r), j.source, j.source_id) for j, r in items])
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


def unscored_passed(con, now, max_age_days, source_max_age=None):
    """Every stored job that passed the pre-filter, has no job_scores row, is not expired and is not older than
    max_age_days. Oldest first_seen first. `now` is a timezone-aware datetime."""
    cur = con.execute(
        "SELECT j.source,j.source_id,j.company,j.title,j.url,j.posted,j.location,j.remote,j.description,j.expires,"
        "j.rule_score FROM jobs j LEFT JOIN job_scores s ON s.source=j.source AND s.source_id=j.source_id "
        "WHERE j.passed=1 AND s.source_id IS NULL AND (j.expires IS NULL OR j.expires='' OR j.expires>?) "
        "ORDER BY j.first_seen", (now.isoformat(),))
    out = []
    for r in cur.fetchall():
        cutoff = now - timedelta(days=(source_max_age or {}).get(r[0], max_age_days))
        try:
            if not r[5] or datetime.fromisoformat(r[5]) < cutoff:
                continue
        except ValueError:
            continue
        out.append(Job(*r[:7], None if r[7] is None else bool(r[7]), r[8], expires=r[9] or "", rule_score=r[10]))
    return out


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


WAITING_DAYS = 7      # a job with only a rule score is listed this long after it was posted

_SCORED = ("SELECT j.source,j.source_id,j.company,j.title,j.url,j.posted,j.rule,s.score_json,s.draft_path,"
           "j.also_seen_on,s.resume_path,j.location,j.expires,j.undated,j.rule_score,j.rule_json "
           "FROM jobs j LEFT JOIN job_scores s ON s.source=j.source AND s.source_id=j.source_id "
           "WHERE (s.score_json IS NOT NULL OR (j.passed=1 AND j.rule_score IS NOT NULL AND j.posted>?)) "
           "AND (j.expires IS NULL OR j.expires='' OR j.expires>?)")


def _since(now_iso, days):
    return (datetime.fromisoformat(now_iso) - timedelta(days=days)).isoformat()


def scored_jobs(con, now_iso=None, waiting_days=WAITING_DAYS):
    """Raw rows, as dicts, for every job the dashboard lists: model-scored jobs, and jobs that passed the pre-filter and
    have only a rule score (posted within waiting_days). Expired jobs (Himalayas expiryDate) are left out."""
    now_iso = now_iso or datetime.now(timezone.utc).isoformat()
    cur = con.execute(_SCORED, (_since(now_iso, waiting_days), now_iso))
    names = [d[0] for d in cur.description]
    return [dict(zip(names, row)) for row in cur.fetchall()]


def get_scored_job(con, source, source_id, now_iso=None, waiting_days=WAITING_DAYS):
    now_iso = now_iso or datetime.now(timezone.utc).isoformat()
    cur = con.execute(_SCORED + " AND j.source=? AND j.source_id=?", (_since(now_iso, waiting_days), now_iso, source, source_id))
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


def model_scored(con):
    """[(Job, fit_score)] for every job the model scored (any age). Used to calibrate the rule score."""
    out = []
    for r in con.execute("SELECT j.source,j.source_id,j.company,j.title,j.url,j.posted,j.location,j.remote,j.description,"
                         "j.expires,json_extract(s.score_json,'$.fit_score') FROM jobs j JOIN job_scores s "
                         "ON s.source=j.source AND s.source_id=j.source_id WHERE s.score_json IS NOT NULL").fetchall():
        out.append((Job(*r[:7], None if r[7] is None else bool(r[7]), r[8], expires=r[9] or ""), r[10]))
    return out
