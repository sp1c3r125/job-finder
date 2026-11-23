"""P4: the hosted worker, per-user and global caps, the run-now queue, and the deletion sweep. Needs TEST_DATABASE_URL.
Two invented users; the model is a fake. Nothing is fetched or sent."""
import pytest

pytest.importorskip("psycopg")
import psycopg  # noqa: E402
import yaml  # noqa: E402

from jobfinder import auth, db, settings, worker  # noqa: E402

from .conftest import GOOD, FakeBackend, make_job  # noqa: E402
from .pg_helpers import A, B, pg  # noqa: E402,F401
from .test_resume import EXTRACTED, SAMPLE  # noqa: E402


def cfg(**over):
    base = yaml.safe_load(open("config.yaml", encoding="utf-8"))
    base.update(rule_min=0, model_daily_jobs=15, user_daily_call_cap=20, global_daily_call_cap=300)
    base.update(over)
    return base


def facts():
    from jobfinder import resume as rm
    text = SAMPLE.read_text(encoding="utf-8")
    return dict(rm.verify(EXTRACTED, text)[0], text=text, source_file="", left_out=[])


def add_user(pg, uid):
    con = pg.worker_conn(uid)
    db.add_consent(con)
    db.set_resume_facts(con, facts())
    con.close()


def add_jobs(pg, n=2):
    con = pg.worker_conn(db.NO_USER)
    for i in range(1, n + 1):
        db.add_to_catalogue(con, make_job(source="greenhouse", source_id=str(i), url=f"https://x.test/{i}",
                                          title=f"AI Automation Engineer {i}"), "2026-11-20T00:00:00+00:00", f"acme|{i}")
    con.close()


class Log:
    def __init__(self):
        self.events = []

    def __call__(self, uid):
        return lambda event, job=None, **f: self.events.append((uid, event, f))


def run(pg, backend, queue_only=False, **over):
    return worker.run_pass(pg.worker, cfg(**over), [], backend, None, Log(), queue_only=queue_only)


def usage(pg, uid, kind):
    con = pg.worker_conn(uid)
    try:
        return con.execute("SELECT count(*) FROM usage WHERE kind=?", (kind,)).fetchone()[0]
    finally:
        con.close()


def scored(pg, uid):
    con = pg.app_conn(uid)
    try:
        return [r["source_id"] for r in db.scored_jobs(con) if r["score_json"]]
    finally:
        con.close()


def test_two_users_are_scored_in_one_run_and_see_only_their_own(pg):
    add_user(pg, A), add_user(pg, B), add_jobs(pg)

    class Counting(FakeBackend):
        last_usage = ("test-model", 100, 20)

    out = run(pg, Counting([GOOD] * 4))
    assert out == {"fetched_new": 0, "scored": {A: 2, B: 2}, "stopped": ""}
    assert sorted(scored(pg, A)) == ["1", "2"] and sorted(scored(pg, B)) == ["1", "2"]
    assert usage(pg, A, "call") == 2 and usage(pg, B, "call") == 2
    assert usage(pg, A, "tokens") == 2                                  # tokens and model are recorded per call
    con = pg.worker_conn(A)
    assert con.execute("SELECT sum(tokens_in), sum(tokens_out), max(model) FROM usage WHERE kind='tokens'").fetchone() == (200, 40, "test-model")


def test_a_user_who_already_used_their_daily_cap_stops_while_the_other_continues(pg):
    add_user(pg, A), add_user(pg, B), add_jobs(pg)
    con = pg.worker_conn(A)
    db.add_usage(con, "call"), db.add_usage(con, "call")
    con.close()
    backend = FakeBackend([GOOD] * 4)
    out = run(pg, backend, user_daily_call_cap=2)
    assert out["scored"] == {A: 0, B: 2} and out["stopped"] == ""
    assert usage(pg, A, "refused") >= 1 and usage(pg, B, "refused") == 0
    assert len(backend.calls) == 2                                      # only B's two jobs reached the model


def test_a_users_own_kill_switch_stops_only_that_user(pg):
    add_user(pg, A), add_user(pg, B), add_jobs(pg)
    con = pg.worker_conn(A)
    db.set_setting(con, "kill", "1")
    con.close()
    out = run(pg, FakeBackend([GOOD] * 4))
    assert out["scored"] == {A: 0, B: 2}


def test_the_global_kill_switch_stops_everyone_before_any_model_call(pg):
    add_user(pg, A), add_user(pg, B), add_jobs(pg)
    pg.admin_exec("INSERT INTO global_settings VALUES ('kill', '1')")
    backend = FakeBackend([GOOD] * 4)
    out = run(pg, backend)
    assert out["stopped"] == "global_kill" and out["scored"] == {} and backend.calls == []


def test_the_global_cap_stops_the_whole_pass(pg):
    add_user(pg, A), add_user(pg, B), add_jobs(pg)
    backend = FakeBackend([GOOD] * 4)
    out = run(pg, backend, global_daily_call_cap=2)
    assert out["stopped"] == "global_cap" and out["scored"] == {A: 2, B: 0}
    assert len(backend.calls) == 2 and usage(pg, B, "refused") >= 1


def test_run_now_is_allowed_once_a_day_and_the_queue_pass_serves_only_those_users(pg):
    add_user(pg, A), add_user(pg, B), add_jobs(pg)
    a = pg.app_conn(A)
    assert db.run_status_today(a) is None
    assert db.request_run(a) is True
    assert db.request_run(a) is False                                   # a second request the same day is refused
    assert db.run_status_today(a) == "queued"
    out = run(pg, FakeBackend([GOOD] * 4), queue_only=True)
    assert out["scored"] == {A: 2} and scored(pg, B) == []             # B did not ask, so B is not served
    assert db.run_status_today(a) == "done"
    assert db.request_run(a) is False                                   # still refused after it ran
    a.close()


def test_only_one_worker_runs_at_a_time(pg):
    first = worker.WorkerLock(pg.worker)
    try:
        with pytest.raises(worker.Busy):
            run(pg, FakeBackend([]))
    finally:
        first.release()
    run(pg, FakeBackend([]))                                            # free again once the first worker is gone


def test_the_app_role_cannot_list_users_or_read_the_queue_of_others(pg):
    add_user(pg, A), add_user(pg, B)
    a, b = pg.app_conn(A), pg.app_conn(B)
    db.request_run(b)
    assert db.queued_users(a) == []                                    # B's request is invisible to A through the app role
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        db.active_users(a)
    a.close(), b.close()


def test_a_deleted_user_is_not_scored_and_the_sweep_removes_the_sign_in_account(pg):
    add_user(pg, A), add_user(pg, B), add_jobs(pg)
    con = pg.app_conn(A)
    db.delete_account_data(con)
    con.close()
    out = run(pg, FakeBackend([GOOD] * 4))
    assert list(out["scored"]) == [B]

    class FakeAuth:
        def __init__(self, fail=False):
            self.deleted, self.fail = [], fail

        def delete_user(self, uid, key):
            if self.fail:
                raise auth.AuthError("down")
            self.deleted.append((uid, key))

    log = Log()(None)
    assert worker.sweep_deletions(pg.worker, FakeAuth(fail=True), "k", log) == 0
    assert worker.sweep_deletions(pg.worker, (ok := FakeAuth()), "k", log) == 1
    assert ok.deleted == [(A, "k")]
    assert worker.sweep_deletions(pg.worker, FakeAuth(), "k", log) == 0   # nothing left to do


def test_the_worker_fetches_only_the_hosted_sources():
    companies = [{"source": "greenhouse", "board": "x"}, {"source": "lever", "board": "y"}, {"source": "himalayas", "board": "z"}]
    assert [c["source"] for c in worker.hosted_companies({"hosted_sources": ["greenhouse", "lever", "ashby"]}, companies)] == ["greenhouse", "lever"]
    assert worker.hosted_companies({}, companies) == []                 # no allowlist, no fetching


def test_hosted_worker_refuses_the_claude_code_backend(monkeypatch, tmp_path):
    import shutil
    shutil.copy("config.yaml", tmp_path / "config.yaml")                # no .env in this folder
    monkeypatch.chdir(tmp_path)
    for k, v in {"JOBFINDER_MODE": "hosted", "BACKEND": "claude-code", "DATABASE_URL": "x", "SUPABASE_URL": "x",
                 "SUPABASE_ANON_KEY": "x", "ANTHROPIC_API_KEY": "x", "SESSION_SECRET": "x", "ALLOWED_HOSTS_EXTRA": "x",
                 "WORKER_DATABASE_URL": "x"}.items():
        monkeypatch.setenv(k, v)
    with pytest.raises(SystemExit, match="BACKEND must be 'api'"):
        worker.main([])


def test_the_worker_is_for_hosted_mode_only(monkeypatch, tmp_path):
    import shutil
    shutil.copy("config.yaml", tmp_path / "config.yaml")
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("JOBFINDER_MODE", raising=False)
    with pytest.raises(SystemExit, match="hosted mode only"):
        worker.main([])
