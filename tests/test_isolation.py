"""P2: Postgres schema, row-level security and the isolation test. Needs TEST_DATABASE_URL (skipped without it; CI sets it)."""
import pytest

pytest.importorskip("psycopg")
from jobfinder import db  # noqa: E402

from .pg_helpers import A, B, NOW, job, leaks, pg, seed  # noqa: E402,F401


def test_users_cannot_reach_each_others_rows(pg):
    seed(pg)
    assert leaks(pg) == []


# The isolation test must fail when protection is removed. Three ways to remove it, on every per-user table.
BREAKS = {
    "policy dropped": lambda t: f"DROP POLICY own_rows ON {t}",
    "policy opened to everyone": lambda t: [f"DROP POLICY own_rows ON {t}",
                                            f"CREATE POLICY own_rows ON {t} FOR ALL TO {db.APP_ROLE} USING (true) WITH CHECK (true)"],
    "row-level security off": lambda t: f"ALTER TABLE {t} DISABLE ROW LEVEL SECURITY",
}


@pytest.mark.parametrize("how", sorted(BREAKS))
@pytest.mark.parametrize("table", db.PER_USER_TABLES)
def test_the_isolation_test_fails_when_protection_is_removed(pg, table, how):
    seed(pg)                                                  # data exists before the protection is broken
    stmts = BREAKS[how](table)
    pg.admin_exec(*([stmts] if isinstance(stmts, str) else stmts))
    assert leaks(pg), f"{how} on {table} was not caught"


def test_every_table_with_a_user_id_is_protected(pg):
    import psycopg
    with psycopg.connect(pg.admin) as con:
        tables = {r[0] for r in con.execute(
            "SELECT table_name FROM information_schema.columns WHERE table_schema='public' AND column_name='user_id'")}
        assert tables == set(db.PER_USER_TABLES)              # a new per-user table must be listed in PER_USER_TABLES
        for t in tables:
            rls, forced = con.execute("SELECT relrowsecurity, relforcerowsecurity FROM pg_class WHERE relname=%s", (t,)).fetchone()
            policies = con.execute("SELECT count(*) FROM pg_policies WHERE tablename=%s", (t,)).fetchone()[0]
            assert rls and forced and policies >= 1, t


def test_app_roles_cannot_bypass_row_level_security(pg):
    import psycopg
    with psycopg.connect(pg.admin) as con:
        for role in (db.APP_ROLE, db.WORKER_ROLE):
            bypass, superuser = con.execute("SELECT rolbypassrls, rolsuper FROM pg_roles WHERE rolname=%s", (role,)).fetchone()
            assert not bypass and not superuser, role
        owners = {r[0] for r in con.execute("SELECT tableowner FROM pg_tables WHERE schemaname='public'")}
        assert db.APP_ROLE not in owners and db.WORKER_ROLE not in owners


def test_app_role_cannot_read_the_migration_table(pg):
    import psycopg
    with psycopg.connect(pg.app) as con, pytest.raises(psycopg.errors.InsufficientPrivilege):
        con.execute("SELECT * FROM schema_version")


def test_migration_is_idempotent(pg):
    assert db.migrate_pg(pg.admin) == 3
    assert db.migrate_pg(pg.admin) == 3


def test_connect_pg_refuses_a_non_uuid_user(pg):
    with pytest.raises(ValueError):
        db.connect_pg(pg.app, "local")


def test_columns_match_sqlite(pg, tmp_path):
    import psycopg
    lite = db.connect(str(tmp_path / "t.db"))
    legacy = {"passed", "rule", "rule_score", "rule_json"}      # old jobs columns, kept unused in SQLite only
    hosted_only = {"draft_text"}                                # hosted cover notes live in the database, not in a file
    with psycopg.connect(pg.admin) as con:
        for t in ("jobs", "user_jobs", "job_scores", "usage", "settings"):
            want = {r[1] for r in lite.execute(f"PRAGMA table_info({t})")} - (legacy if t == "jobs" else set())
            got = {r[0] for r in con.execute(
                "SELECT column_name FROM information_schema.columns WHERE table_schema='public' AND table_name=%s", (t,))}
            got -= hosted_only if t == "job_scores" else set()
            assert got == want, t


def test_db_functions_work_the_same_on_postgres(pg):
    """The two-user test from test_db_users.py, on Postgres."""
    a, b = pg.worker_conn(A), pg.worker_conn(B)
    j = job(1)
    db.save(a, j, "2026-11-19T00:00:00+00:00", True, "ok")
    db.requalify(b, j, "2026-11-19T00:00:00+00:00", False, True, "ok")
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
    assert [r["score_json"] for r in db.scored_jobs(b, NOW.isoformat())] == [None]
    assert db.get_scored_job(b, "greenhouse", "1", NOW.isoformat())["draft_path"] is None
    assert db.model_scored(b) == [] and [s for _, s in db.model_scored(a)] == [95]
    assert db.unscored_passed(a, NOW, 7) == [] and len(db.unscored_passed(b, NOW, 7)) == 1
    db.set_draft(b, j, "b.md")
    db.set_resume(b, j, "b.docx")
    assert db.get_scored_job(b, "greenhouse", "1", NOW.isoformat())["resume_path"] == "b.docx"
    assert db.find_job(a, j.url).source_id == "1" and db.get_job(a, "greenhouse", "1").company == "Acme1"
    assert db.is_seen(a, j)
