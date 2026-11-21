import json
import sqlite3

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
  source TEXT, source_id TEXT, company TEXT, title TEXT, url TEXT, posted TEXT,
  location TEXT, remote INTEGER, description TEXT, also_seen_on TEXT DEFAULT '[]',
  first_seen TEXT, passed INTEGER, rule TEXT,
  score_json TEXT, draft_path TEXT,
  PRIMARY KEY (source, source_id)
)"""


def connect(path="jobs.db"):
    con = sqlite3.connect(path)
    con.execute(SCHEMA)
    return con


def is_seen(con, job):
    return con.execute("SELECT 1 FROM jobs WHERE source=? AND source_id=?", (job.source, job.source_id)).fetchone() is not None


def save(con, job, now_iso, passed, rule):
    con.execute(
        "INSERT INTO jobs (source,source_id,company,title,url,posted,location,remote,description,also_seen_on,first_seen,passed,rule)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (job.source, job.source_id, job.company, job.title, job.url, job.posted, job.location,
         None if job.remote is None else int(job.remote), job.description, json.dumps(job.also_seen_on),
         now_iso, int(passed), rule),
    )
    con.commit()


def set_score(con, job, score, draft_path=None):
    con.execute("UPDATE jobs SET score_json=?, draft_path=? WHERE source=? AND source_id=?",
                (json.dumps(score), draft_path, job.source, job.source_id))
    con.commit()
