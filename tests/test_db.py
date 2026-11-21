import json
import re
import sqlite3
from pathlib import Path

from jobfinder import db
from .conftest import GOOD, make_job

ROOT = Path(__file__).resolve().parent.parent
NOW = "2026-11-21T00:00:00+00:00"


def test_no_sql_outside_db_py():
    sql = re.compile(r"\b(SELECT\s.+\sFROM|INSERT\s+INTO|UPDATE\s+\w+\s+SET|DELETE\s+FROM|CREATE\s+TABLE)\b|import sqlite3|\.execute\(",
                     re.IGNORECASE)
    offenders = [p.name for p in (ROOT / "jobfinder").rglob("*.py") if p.name != "db.py" and sql.search(p.read_text(encoding="utf-8"))]
    assert offenders == []


def test_scores_live_in_their_own_table(tmp_path):
    con = db.connect(str(tmp_path / "t.db"))
    job = make_job()
    db.save(con, job, NOW, True, "worldwide")
    assert db.scored_jobs(con) == []                       # catalogue row alone is not a scored job
    db.set_score(con, job, GOOD, "drafts/a.md", "resumes/a.docx")
    [row] = db.scored_jobs(con)
    assert json.loads(row["score_json"])["fit_score"] == 82 and row["draft_path"] == "drafts/a.md"
    db.set_score(con, job, dict(GOOD, fit_score=90))       # a second score replaces the first
    assert len(db.scored_jobs(con)) == 1
    db.set_resume(con, job, "resumes/b.docx")
    assert db.scored_jobs(con)[0]["resume_path"] == "resumes/b.docx"


def test_expired_jobs_are_hidden(tmp_path):
    con = db.connect(str(tmp_path / "t.db"))
    old = make_job(source_id="old", expires="2026-11-01T00:00:00+00:00")
    live = make_job(source_id="live", title="Other", expires="2027-01-01T00:00:00+00:00")
    none = make_job(source_id="none", title="Third")
    for j in (old, live, none):
        db.save(con, j, NOW, True, "worldwide")
        db.set_score(con, j, GOOD)
    assert {r["source_id"] for r in db.scored_jobs(con, NOW)} == {"live", "none"}
    assert db.get_scored_job(con, "ashby", "old", NOW) is None
    assert db.get_scored_job(con, "ashby", "live", NOW)["description"]


def test_old_database_is_migrated(tmp_path):
    path = str(tmp_path / "old.db")
    raw = sqlite3.connect(path)
    raw.execute("CREATE TABLE jobs (source TEXT, source_id TEXT, company TEXT, title TEXT, url TEXT, posted TEXT,"
                " location TEXT, remote INTEGER, description TEXT, also_seen_on TEXT DEFAULT '[]', first_seen TEXT,"
                " passed INTEGER, rule TEXT, score_json TEXT, draft_path TEXT, norm_key TEXT, resume_path TEXT,"
                " PRIMARY KEY (source, source_id))")
    raw.execute("INSERT INTO jobs (source,source_id,company,title,url,posted,location,remote,description,score_json,draft_path)"
                " VALUES ('ashby','1','Acme','AI Eng','u','2026-11-20T00:00:00+00:00','x',1,'d',?,'drafts/x.md')",
                (json.dumps(GOOD),))
    raw.commit()
    raw.close()
    con = db.connect(path)
    [row] = db.scored_jobs(con, NOW)
    assert row["draft_path"] == "drafts/x.md"
    assert db.connect(path) is not None                    # a second connect does not duplicate or fail
    assert len(db.scored_jobs(db.connect(path), NOW)) == 1
