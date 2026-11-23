"""Helpers for the Postgres tests. They run only when TEST_DATABASE_URL points at a Postgres the tests may create databases in
(for example postgresql://postgres:pw@localhost:5432/postgres). Invented data only."""
import os
import uuid
from datetime import datetime, timezone

import pytest

from jobfinder import db
from jobfinder.models import Job

A = "11111111-1111-1111-1111-111111111111"
B = "22222222-2222-2222-2222-222222222222"
ROLE_PASSWORD = "test-only-password"
NOW = datetime(2026, 11, 20, tzinfo=timezone.utc)
POSTED = "2026-11-19T00:00:00+00:00"


def admin_url():
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL is not set")
    pytest.importorskip("psycopg")
    return url


def url_for(base, dbname, user=None, password=None):
    from psycopg.conninfo import make_conninfo
    kw = {"dbname": dbname}
    if user:
        kw.update(user=user, password=password)
    return make_conninfo(base, **kw)


class Pg:
    def __init__(self, base, dbname):
        self.base, self.dbname = base, dbname
        self.admin = url_for(base, dbname)
        self.app = url_for(base, dbname, db.APP_ROLE, ROLE_PASSWORD)
        self.worker = url_for(base, dbname, db.WORKER_ROLE, ROLE_PASSWORD)

    def admin_exec(self, *statements):
        import psycopg
        with psycopg.connect(self.admin) as con:
            for s in statements:
                con.execute(s)

    def app_conn(self, user):
        return db.connect_pg(self.app, user)

    def worker_conn(self, user):
        return db.connect_pg(self.worker, user)


@pytest.fixture
def pg():
    import psycopg
    base = admin_url()
    name = "jf_test_" + uuid.uuid4().hex[:12]
    with psycopg.connect(base, autocommit=True) as con:
        con.execute(f'CREATE DATABASE "{name}"')
    try:
        p = Pg(base, name)
        db.migrate_pg(p.admin)
        for role in (db.APP_ROLE, db.WORKER_ROLE):
            db.set_pg_role_login(p.admin, role, ROLE_PASSWORD)
        yield p
    finally:
        with psycopg.connect(base, autocommit=True) as con:
            con.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


def job(n):
    return Job(source="greenhouse", source_id=str(n), company=f"Acme{n}", title=f"Automation Engineer {n}",
               url=f"https://example.test/{n}", posted=POSTED, location="Remote", remote=True, description="d")


def seed(pg):
    """User A owns job 1 and user B owns job 2, with a score, usage and a setting each."""
    for user, n in ((A, 1), (B, 2)):
        con = pg.worker_conn(user)
        j = job(n)
        db.save(con, j, POSTED, True, "ok")
        db.set_rule_scores(con, [(j, {"score": 50 + n})])
        db.set_score(con, j, {"fit_score": 80 + n}, draft_path=f"d{n}.md")
        db.add_usage(con, "call")
        db.set_setting(con, "kill", str(n))
        db.add_consent(con)
        db.request_run(con)
        db.set_resume_facts(con, {"name": f"Person {n}"})
        con.execute("INSERT INTO deletion_requests (user_id,requested_at) VALUES (?,?)", (user, "t"))
        con.commit()
        con.close()


def leaks(pg):
    """Everything wrong with the isolation, as a list of strings (empty = isolated). Call seed(pg) first.
    Checks the app functions and the database role directly, for every table that holds one user's rows."""
    import psycopg
    bad = []
    a = pg.app_conn(A)
    try:
        # through the app functions: A sees A's rows and nothing of B's
        listed = [r["source_id"] for r in db.scored_jobs(a, NOW.isoformat())]
        if listed != ["1"]:
            bad.append(f"scored_jobs for A returned {listed}")
        if [j.source_id for j in db.passed_jobs(a)] != ["1"]:
            bad.append("passed_jobs for A is not exactly job 1")
        if db.get_scored_job(a, "greenhouse", "2", NOW.isoformat()) is not None:
            bad.append("A can read B's scored job")
        if db.calls_today(a) != 1:
            bad.append(f"calls_today for A is {db.calls_today(a)}")
        if db.get_setting(a, "kill") != "1":
            bad.append("A's setting is not A's")
        a.commit()
        # straight at the tables, as the app role, with A's context set
        for t in db.PER_USER_TABLES:
            for verb, sql in (("select", f"SELECT count(*) FROM {t} WHERE user_id = %s"),):
                if a.execute(sql.replace("%s", "?"), (B,)).fetchone()[0] != 0:
                    bad.append(f"A can {verb} B's rows in {t}")
            if a.execute(f"SELECT count(*) FROM {t}").fetchone()[0] == 0:
                bad.append(f"A cannot see its own rows in {t}")
            cur = a.execute(f"DELETE FROM {t} WHERE user_id = ?", (B,))
            if cur.rowcount != 0:
                bad.append(f"A deleted B's rows in {t}")
            a.rollback()
        for t, sets in (("user_jobs", "rule = 'x'"), ("job_scores", "draft_path = 'x'"),
                        ("usage", "note = 'x'"), ("settings", "value = 'x'")):
            cur = a.execute(f"UPDATE {t} SET {sets} WHERE user_id = ?", (B,))
            if cur.rowcount != 0:
                bad.append(f"A changed B's rows in {t}")
            a.rollback()
        for t, cols, vals in (("settings", "(user_id,key,value)", "(?,'k','v')"), ("usage", "(user_id,day,ts,kind,note)", "(?,'d','t','call','n')")):
            try:
                a.execute(f"INSERT INTO {t} {cols} VALUES {vals}", (B,))
                bad.append(f"A inserted a row for B in {t}")
            except psycopg.Error:
                pass
            a.rollback()
        # the catalogue is read-only for the app role
        try:
            a.execute("INSERT INTO jobs (source, source_id) VALUES ('x', 'x')")
            bad.append("the app role can write the shared catalogue")
        except psycopg.Error:
            pass
        a.rollback()
    finally:
        a.close()
    # no user context at all: nothing is visible
    with psycopg.connect(pg.app) as raw:
        for t in db.PER_USER_TABLES:
            if raw.execute(f"SELECT count(*) FROM {t}").fetchone()[0] != 0:
                bad.append(f"rows of {t} are visible with no user set")
    return bad
