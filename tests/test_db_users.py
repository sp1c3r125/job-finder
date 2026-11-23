"""P1: user_id in the schema. Invented data only."""
import json
import os
import shutil
import sqlite3
from datetime import datetime, timezone

from jobfinder import db
from jobfinder.models import Job

NOW = datetime(2026, 11, 20, tzinfo=timezone.utc)
POSTED = "2026-11-19T00:00:00+00:00"


def job(n, **kw):
    base = dict(source="greenhouse", source_id=str(n), company=f"Acme{n}", title=f"Automation Engineer {n}",
                url=f"https://example.test/{n}", posted=POSTED, location="Remote", remote=True, description="d")
    base.update(kw)
    return Job(**base)


def make_v1(path):
    """A jobs.db as it was before users: the old tables, filled with invented rows."""
    con = sqlite3.connect(path)
    for stmt in db.SCHEMA_V1.split(";"):
        if stmt.strip():
            con.execute(stmt)
    for n, passed, rs in ((1, 1, 80), (2, 1, 55), (3, 0, None)):
        con.execute("INSERT INTO jobs (source,source_id,company,title,url,posted,location,remote,description,first_seen,"
                    "passed,rule,norm_key,rule_score,rule_json) VALUES ('greenhouse',?,?,?,?,?,'Remote',1,'d',?,?,?,?,?,?)",
                    (str(n), f"Acme{n}", f"Automation Engineer {n}", f"https://example.test/{n}", POSTED,
                     POSTED, passed, "ok", f"k{n}", rs, json.dumps({"score": rs}) if rs else None))
    con.execute("INSERT INTO job_scores VALUES ('greenhouse','1',?,'drafts/a.md','resumes/a.docx',?)",
                (json.dumps({"fit_score": 88}), POSTED))
    con.execute("INSERT INTO usage VALUES ('2026-11-20','t','call','n')")
    con.execute("INSERT INTO settings VALUES ('kill','1')")
    con.commit()
    con.close()


def listing(path):
    con = db.connect(path)
    rows = db.scored_jobs(con, NOW.isoformat())
    return sorted((r["source_id"], r["score_json"], r["rule_score"], r["draft_path"], r["resume_path"]) for r in rows)


def test_migration_keeps_everything_and_lists_the_same_jobs(tmp_path):
    path = str(tmp_path / "jobs.db")
    make_v1(path)
    before = sqlite3.connect(path).execute(
        "SELECT source_id, passed, rule_score FROM jobs ORDER BY source_id").fetchall()
    # what the old schema listed: the same query, written for the old columns
    old = sqlite3.connect(path).execute(
        "SELECT j.source_id, s.score_json, j.rule_score, s.draft_path, s.resume_path FROM jobs j "
        "LEFT JOIN job_scores s ON s.source=j.source AND s.source_id=j.source_id "
        "WHERE s.score_json IS NOT NULL OR (j.passed=1 AND j.rule_score IS NOT NULL AND j.posted>?)",
        ("2026-11-13T00:00:00+00:00",)).fetchall()

    assert sorted(old) == listing(path)                       # the dashboard shows the same jobs
    con = db.connect(path)
    assert db.schema_version(con) == db.MIGRATIONS[-1][0]
    assert con.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 3
    assert con.execute("SELECT source_id, passed, rule_score FROM user_jobs WHERE user_id='local' "
                       "ORDER BY source_id").fetchall() == before
    assert con.execute("SELECT user_id, COUNT(*) FROM usage GROUP BY user_id").fetchall() == [("local", 1)]
    assert db.get_setting(con, "kill") == "1"
    assert con.execute("SELECT user_id FROM job_scores").fetchall() == [("local",)]


def test_migration_makes_a_backup_once_and_is_idempotent(tmp_path):
    path = str(tmp_path / "jobs.db")
    make_v1(path)
    db.connect(path).close()
    bak = path + ".bak-v2"
    assert os.path.exists(bak)
    old = sqlite3.connect(bak)                                # the copy is the old schema, untouched
    assert old.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 3
    assert not old.execute("SELECT 1 FROM sqlite_master WHERE name='user_jobs'").fetchone()
    size = os.path.getsize(bak)
    old.close()
    db.connect(path).close()                                  # second start: nothing to do
    assert os.path.getsize(bak) == size
    assert db.schema_version(db.connect(path)) == db.MIGRATIONS[-1][0]


def test_migration_works_on_a_copy_and_leaves_the_original(tmp_path):
    src = str(tmp_path / "orig.db")
    make_v1(src)
    copy = str(tmp_path / "copy.db")
    shutil.copy(src, copy)
    db.connect(copy).close()
    assert not sqlite3.connect(src).execute("SELECT 1 FROM sqlite_master WHERE name='user_jobs'").fetchone()


def test_new_database_gets_no_backup(tmp_path):
    path = str(tmp_path / "new.db")
    db.connect(path).close()
    assert not os.path.exists(path + ".bak-v2")


def test_a_failed_migration_rolls_back(tmp_path, monkeypatch):
    path = str(tmp_path / "jobs.db")
    make_v1(path)

    def boom(con):
        con.execute("DROP TABLE job_scores")
        raise RuntimeError("stop")
    monkeypatch.setattr(db, "MIGRATIONS", [(1, db._migrate_1), (2, boom)])
    try:
        db.connect(path)
    except RuntimeError:
        pass
    con = sqlite3.connect(path)
    assert con.execute("SELECT COUNT(*) FROM job_scores").fetchone()[0] == 1
    assert con.execute("SELECT MAX(version) FROM schema_version").fetchone()[0] == 1


def test_two_users_do_not_see_each_others_rows(tmp_path):
    path = str(tmp_path / "t.db")
    a, b = db.connect(path, "user-a"), db.connect(path, "user-b")
    j = job(1)
    db.save(a, j, POSTED, True, "ok")
    db.requalify(b, j, POSTED, False, True, "ok")             # B also passes it: the catalogue row is shared
    db.set_rule_scores(a, [(j, {"score": 90})])
    db.set_rule_scores(b, [(j, {"score": 10})])
    db.set_score(a, j, {"fit_score": 95}, draft_path="a.md")
    db.add_usage(a, "call")
    db.set_setting(a, "kill", "1")

    assert [x.rule_score for x in db.passed_jobs(a)] == [90]
    assert [x.rule_score for x in db.passed_jobs(b)] == [10]
    assert db.calls_today(a) == 1 and db.calls_today(b) == 0
    assert db.get_setting(a, "kill") == "1" and db.get_setting(b, "kill") == ""
    assert db.model_scored_today(a) == 1 and db.model_scored_today(b) == 0
    assert len(db.scored_jobs(a, NOW.isoformat())) == 1
    assert [r["score_json"] for r in db.scored_jobs(b, NOW.isoformat())] == [None]   # rule score only, no model score
    assert db.get_scored_job(b, "greenhouse", "1", NOW.isoformat())["draft_path"] is None
    assert db.model_scored(b) == [] and len(db.model_scored(a)) == 1
    assert db.unscored_passed(a, NOW, 7) == [] and len(db.unscored_passed(b, NOW, 7)) == 1


def test_default_user_is_local(tmp_path):
    con = db.connect(str(tmp_path / "t.db"))
    assert con.user_id == "local"
    db.save(con, job(1), POSTED, True, "ok")
    assert con.execute("SELECT user_id FROM user_jobs").fetchall() == [("local",)]
