"""Hosted worker: the only process that fetches jobs and spends the owner's model budget on a schedule.

    python -m jobfinder.worker            daily pass: fetch the shared catalogue once, then each active user in turn
    python -m jobfinder.worker --queue    only the users who pressed "Run now"; no fetching

One worker at a time (a Postgres advisory lock). Each user is handled on their own connection, so row-level security
applies exactly as in the web app. Limits, in the order they are checked before every model call: the global kill switch,
the global daily cap, the user's kill switch, the user's daily cap. A global limit ends the whole pass; a user's own limit
ends only that user. The worker never sends or applies anywhere. It also finishes account deletions (the sign-in account)."""
import argparse
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml

from . import db, pipeline, rules, settings
from .auth import AuthError
from .dedupe import dedupe, norm_key
from .guard import UserCappedBackend
from .prefilter import max_age_days, prefilter
from .resume import profile_facts, with_resume_keywords
from .scoring import score_job
from .target import with_target

LOCK_KEY = 4_210_417                                   # any fixed number; every worker uses the same one
NO_FILES = Path("<hosted mode reads no files>")       # a folder that cannot hold target.yaml: hosted users have no files
NO_FACTS = {"years_total": None, "years_ai": None, "known_gaps": []}   # profile.yaml facts: there is no such file per user
DEFAULT_USER_CAP, DEFAULT_GLOBAL_CAP = 20, 300


class Busy(RuntimeError):
    """Another worker holds the lock."""


class WorkerLock:
    """A Postgres advisory lock held on its own connection for as long as the object lives."""

    def __init__(self, url):
        self.con = db.try_advisory_lock(url, LOCK_KEY)
        if self.con is None:
            raise Busy("another worker is running")

    def release(self):
        self.con.close()                               # closing the session frees the lock


def hosted_companies(cfg, companies):
    """Only companies on a source the hosted allowlist names. Aggregators are never fetched in hosted mode."""
    allow = set(cfg.get("hosted_sources") or [])
    return [c for c in companies if c["source"] in allow]


def update_catalogue(url, cfg, companies, http, log, now):
    """Fetch once for everybody and store each job once. Returns the number of new jobs."""
    from .main import collect
    jobs, _ = collect(http, {**cfg, "aggregators": []}, hosted_companies(cfg, companies), log)
    jobs = dedupe(jobs)
    con = db.connect_pg(url, db.NO_USER)
    new = 0
    try:
        for job in jobs:
            if not job.posted or datetime.fromisoformat(job.posted) < now - timedelta(days=max_age_days(cfg, job.source)):
                continue                                 # the shared pre-filter: too old for anyone
            if db.is_seen(con, job) or db.merge_duplicate(con, job, norm_key(job)):
                continue
            db.add_to_catalogue(con, job, now.isoformat(), norm_key(job))
            new += 1
    finally:
        con.close()
    log("catalogue_updated", None, fetched=len(jobs), new=new)
    return new


def run_user(url, user_id, cfg, backend, log, now, user_cap, global_cap):
    """One user's part: their pre-filter on the catalogue, their rule scores, then model scoring within the caps.
    Returns (scored, stop_reason). stop_reason is "" unless a cap or kill switch stopped this user."""
    con = db.connect_pg(url, user_id)
    try:
        res = db.get_resume_facts(con)
        if not res:
            return 0, ""
        ucfg = with_target(with_resume_keywords(cfg, res), res, NO_FILES)
        horizon = (now - timedelta(days=max(max_age_days(ucfg, s) for s in ucfg["hosted_sources"]))).isoformat()
        seen = db.user_job_keys(con)
        db.set_user_jobs(con, [(j, *prefilter(j, ucfg, now)) for j in db.catalogue_jobs(con, horizon)
                               if (j.source, j.source_id) not in seen])
        db.set_rule_scores(con, [(j, rules.rule_score(j, ucfg, res, NO_FACTS)) for j in db.passed_jobs(con)])
        profile_text = yaml.safe_dump({"resume": profile_facts(res)}, sort_keys=False, allow_unicode=True)
        _, _, _, to_score, _, _ = pipeline.model_gate(con, ucfg, now, log)
        capped = UserCappedBackend(backend, lambda: db.connect_pg(url, user_id), user_cap, log, global_cap)
        scored = 0
        for job in to_score:
            if capped.stopped:
                break
            score = score_job(capped, profile_text, job, log)
            if score is None:
                continue
            db.set_score(con, job, score)
            scored += 1
        log("user_done", None, scored=scored, reason=capped.stop_reason)
        return scored, capped.stop_reason
    finally:
        con.close()


def run_pass(url, cfg, companies, backend, http, make_log, queue_only=False, now=None):
    """One pass of the worker. make_log(user_id) gives a logger for that user (None: the worker's own).
    Returns {"fetched_new": n, "scored": {user_id: n}, "stopped": reason}. Raises Busy if another worker is running."""
    log = make_log(None)
    now = now or datetime.now(timezone.utc)
    lock = WorkerLock(url)
    out = {"fetched_new": 0, "scored": {}, "stopped": ""}
    try:
        user_cap = int(cfg.get("user_daily_call_cap", DEFAULT_USER_CAP))
        global_cap = int(cfg.get("global_daily_call_cap", DEFAULT_GLOBAL_CAP))
        who = db.connect_pg(url, db.NO_USER)
        try:
            if db.global_killed(who):
                out["stopped"] = "global_kill"
                log("run_stopped", None, reason="global_kill")
                return out
            users = db.queued_users(who) if queue_only else db.active_users(who)
        finally:
            who.close()
        if not queue_only:
            out["fetched_new"] = update_catalogue(url, cfg, companies, http, log, now)
        for uid in users:
            scored, why = run_user(url, uid, cfg, backend, make_log(uid), now, user_cap, global_cap)
            out["scored"][uid] = scored
            if queue_only:
                con = db.connect_pg(url, uid)
                try:
                    db.finish_runs(con)
                finally:
                    con.close()
            if why in UserCappedBackend.GLOBAL:          # nobody can use the model any more today
                out["stopped"] = why
                break
        return out
    finally:
        lock.release()


def sweep_deletions(url, auth, service_key, log):          # log: the worker's own logger (no user)
    """Remove the sign-in account of every user who deleted their data. The only place the admin key is used."""
    con = db.connect_pg(url, db.NO_USER)
    done = 0
    try:
        for uid in db.pending_deletions(con):
            try:
                auth.delete_user(uid, service_key)
            except AuthError as e:
                log("deletion_failed", None, reason="sign-in service refused")
                continue
            db.finish_deletion(con, uid)
            done += 1
    finally:
        con.close()
    log("deletions_swept", None, removed=done)
    return done


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python -m jobfinder.worker")
    ap.add_argument("--queue", action="store_true", help="only users who pressed Run now (no fetching)")
    args = ap.parse_args(sys.argv[1:] if argv is None else argv)
    settings.load_env()
    with open("config.yaml", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    if settings.mode() != "hosted":
        raise SystemExit("the worker is for hosted mode only (set JOBFINDER_MODE=hosted)")
    settings.check_startup(cfg=cfg)                    # refuses BACKEND=claude-code and lists every missing setting
    worker_url = os.environ.get("WORKER_DATABASE_URL", "").strip()
    if not worker_url:
        raise SystemExit("WORKER_DATABASE_URL is missing (Postgres connection for the worker role)")
    with open("companies.yaml", encoding="utf-8") as f:
        companies = yaml.safe_load(f)["companies"]
    from .backends import get_backend
    from .hosted import make_hosted_log, stdout_sink
    from .http import HttpClient
    def make_log(user_id):
        return make_hosted_log(user_id, stdout_sink)
    try:
        if not args.queue and os.environ.get("SUPABASE_SERVICE_KEY", "").strip():
            from .auth import SupabaseAuth
            sweep_deletions(worker_url, SupabaseAuth(os.environ["SUPABASE_URL"], os.environ["SUPABASE_ANON_KEY"]),
                            os.environ["SUPABASE_SERVICE_KEY"], make_log(None))
        out = run_pass(worker_url, cfg, companies, get_backend(), HttpClient(cfg["user_agent"], cfg["request_delay_seconds"]),
                       make_log, queue_only=args.queue)
    except Busy as e:
        print(str(e))
        return 0
    print(f"worker pass finished: {sum(out['scored'].values())} jobs scored for {len(out['scored'])} users"
          + (f", stopped: {out['stopped']}" if out["stopped"] else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
