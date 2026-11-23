"""P5: the retention job and the privacy page. Needs TEST_DATABASE_URL. Invented data only."""
from datetime import datetime, timedelta, timezone

import pytest

pytest.importorskip("psycopg")
import psycopg  # noqa: E402

from jobfinder import db, worker  # noqa: E402

from .conftest import make_job  # noqa: E402
from .pg_helpers import A, B, pg  # noqa: E402,F401
from .test_hosted import csrf, env, lines, make, sign_in, supa  # noqa: E402,F401

NOW = datetime(2026, 11, 23, 12, tzinfo=timezone.utc)
CFG = {}


def log(event, job=None, **fields):
    pass


def ago(days):
    return (NOW - timedelta(days=days)).isoformat()


def count(pg, sql, *args):
    with psycopg.connect(pg.admin) as con:
        return con.execute(sql, args).fetchone()[0]


def put_job(pg, n, first_seen, scored_by=()):
    w = pg.worker_conn(db.NO_USER)
    j = make_job(source="greenhouse", source_id=str(n), url=f"https://x.test/{n}", title=f"Automation Engineer {n}")
    db.add_to_catalogue(w, j, first_seen, f"acme|{n}")
    w.close()
    for user in scored_by:
        c = pg.worker_conn(user)
        db.set_user_jobs(c, [(j, True, "ok")])
        db.set_score(c, j, {"fit_score": 80})
        c.close()
    return j


def test_stale_jobs_go_but_a_job_with_a_saved_score_stays(pg):
    put_job(pg, 1, ago(40))                          # old, nobody scored it: deleted
    put_job(pg, 2, ago(40), scored_by=[A])           # old, A has a saved score: kept, with A's rows
    put_job(pg, 3, ago(5))                           # recent, unscored: kept
    c = pg.worker_conn(B)
    db.set_user_jobs(c, [(make_job(source="greenhouse", source_id="1", url="https://x.test/1"), True, "ok")])
    c.close()
    out = worker.run_retention(pg.worker, CFG, log, NOW)
    assert out["jobs"] == 1
    with psycopg.connect(pg.admin) as con:
        assert sorted(r[0] for r in con.execute("SELECT source_id FROM jobs").fetchall()) == ["2", "3"]
    assert count(pg, "SELECT count(*) FROM user_jobs WHERE source_id='1'") == 0          # B's row on the deleted job is gone too
    assert count(pg, "SELECT count(*) FROM job_scores WHERE user_id=%s", A) == 1


def test_old_usage_rows_go_per_the_cutoff_and_for_every_user(pg):
    for user in (A, B):
        c = pg.worker_conn(user)
        for d in (100, 91, 89, 1):
            c.execute("INSERT INTO usage (user_id,day,ts,kind,note) VALUES (?,?,?,?,?)",
                      (user, (NOW - timedelta(days=d)).date().isoformat(), ago(d), "call", ""))
        c.commit()
        c.close()
    out = worker.run_retention(pg.worker, CFG, log, NOW)
    assert out["usage"] == 4
    assert count(pg, "SELECT count(*) FROM usage WHERE user_id=%s", A) == 2
    assert count(pg, "SELECT count(*) FROM usage WHERE user_id=%s", B) == 2


def activity(pg, user, last_seen, warned_at=None):
    with psycopg.connect(pg.admin) as con:
        con.execute("DELETE FROM account_activity WHERE user_id=%s", (user,))
        con.execute("INSERT INTO account_activity (user_id,last_seen,warned_at) VALUES (%s,%s,%s)", (user, last_seen, warned_at))


def give_data(pg, user):
    c = pg.worker_conn(user)
    db.add_consent(c)
    db.set_resume_facts(c, {"name": "Person"})
    db.add_usage(c, "call")
    c.close()


def test_inactive_account_is_warned_first_then_deleted_after_the_warning_period(pg):
    give_data(pg, A), give_data(pg, B)
    activity(pg, A, ago(160))                          # inactive past 150 days: warned now, not deleted yet
    activity(pg, B, ago(10))
    out = worker.run_retention(pg.worker, CFG, log, NOW)
    assert out["warned"] == 1 and out["accounts"] == 0
    assert count(pg, "SELECT count(*) FROM resume_facts") == 2
    assert worker.run_retention(pg.worker, CFG, log, NOW)["accounts"] == 0       # a second pass the same day still does not delete
    later = NOW + timedelta(days=31)                   # A is now past 180 days and was warned over 30 days ago
    activity(pg, B, (later - timedelta(days=2)).isoformat())                      # B stays active
    out = worker.run_retention(pg.worker, CFG, log, later)
    assert out["accounts"] == 1
    assert count(pg, "SELECT count(*) FROM resume_facts WHERE user_id=%s", A) == 0
    assert count(pg, "SELECT count(*) FROM resume_facts WHERE user_id=%s", B) == 1          # nothing of B's was touched
    assert count(pg, "SELECT count(*) FROM deletion_requests WHERE user_id=%s", A) == 1     # the sweep removes the sign-in account
    assert count(pg, "SELECT count(*) FROM deletion_requests WHERE user_id=%s", B) == 0


def test_signing_in_cancels_the_warning(pg):
    give_data(pg, A)
    activity(pg, A, ago(160))
    worker.run_retention(pg.worker, CFG, log, NOW)
    assert count(pg, "SELECT warned_at IS NOT NULL FROM account_activity WHERE user_id=%s", A) is True
    c = pg.worker_conn(A)
    db.touch_activity(c)
    c.close()
    assert count(pg, "SELECT warned_at IS NULL FROM account_activity WHERE user_id=%s", A) is True
    assert worker.run_retention(pg.worker, CFG, log, NOW + timedelta(days=90))["accounts"] == 0   # not deleted: they signed in


def test_periods_come_from_config(pg):
    put_job(pg, 1, ago(10))
    assert worker.run_retention(pg.worker, {"retention": {"unscored_jobs_days": 7}}, log, NOW)["jobs"] == 1


def test_the_app_role_cannot_run_the_retention_functions(pg):
    for sql in ("SELECT purge_stale_jobs('2100-01-01')", "SELECT purge_old_usage('2100-01-01')",
                "SELECT warn_inactive('2100','x')", "SELECT inactive_users('2100','2100')"):
        a = pg.app_conn(A)
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            a.execute(sql)
        a.close()


# ---- privacy page --------------------------------------------------------------------------------------------------------
def test_privacy_page_is_public_and_states_the_facts(env, supa, lines):
    c = make(env, supa, lines)
    r = c.get("/privacy")                                       # no sign-in needed
    assert r.status_code == 200
    for word in ("Anthropic", "Supabase", "30 days", "90 days", "180 days", "Account page", "help@example.test", db.CONSENT_VERSION):
        assert word in r.text
    assert "legal basis" not in r.text.lower()                  # that is a question for the lawyer, not decided on the page
    assert 'href="/privacy"' in c.get("/privacy").text          # linked from the footer on every page


def test_consent_page_links_to_privacy_and_names_retention(env, supa, lines):
    c = make(env, supa, lines)
    sign_in(c, supa, consent=False)
    r = c.get("/consent")
    assert 'href="/privacy"' in r.text and "180 days" in r.text


def test_signing_in_records_activity(env, supa, lines, pg):
    c = make(env, supa, lines)
    sign_in(c, supa)
    assert count(pg, "SELECT count(*) FROM account_activity") == 1
