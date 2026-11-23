"""Cost cap and kill switch around any model backend. Counters live in db.py (usage, settings tables)."""
import os

import yaml

from . import db
from .backends import get_backend

DEFAULT_CAP = 300


class Refused(RuntimeError):
    """The wrapper refused a model call (kill switch or daily cap). Nothing was sent to the model."""


def killed(con, kill_file="KILL"):
    return os.path.exists(kill_file) or db.get_setting(con, "kill") == "1"


class CappedBackend:
    """complete() checks the kill switch (settings table or a KILL file) and today's call count first.
    Every allowed call adds a usage row and a log line; every refusal does too. `stopped` becomes True after
    a refusal so a run can stop instead of trying every remaining job."""

    def __init__(self, inner, db_path, cap, log, kill_file="KILL"):
        self.inner, self.db_path, self.cap, self.log, self.kill_file = inner, db_path, cap, log, kill_file
        self.stopped = False

    def complete(self, *args, **kwargs):
        con = db.connect(self.db_path)
        try:
            reason = "kill_switch" if killed(con, self.kill_file) else (
                "daily_cap" if db.calls_today(con) >= self.cap else "")
            if reason:
                self.stopped = True
                db.add_usage(con, "refused", reason)
                self.log("model_refused", None, reason=reason, cap=self.cap)
                raise Refused(reason)
            db.add_usage(con, "call")
            self.log("model_call", None, used_today=db.calls_today(con), cap=self.cap)
        finally:
            con.close()
        inner = self.inner() if callable(self.inner) else self.inner
        return inner.complete(*args, **kwargs)


class UserCappedBackend:
    """Hosted mode: the same checks as CappedBackend, for one signed-in user, on a connection from `connect()`
    (a function returning a new connection for that user). The cap and the kill switch are that user's own.
    With `global_cap` set (the worker), the global kill switch and the total across all users are checked too.
    `stop_reason` says why it stopped; "global_cap" and "global_kill" mean no user should continue."""

    GLOBAL = ("global_cap", "global_kill")

    def __init__(self, inner, connect, cap, log, global_cap=None):
        self.inner, self.connect, self.cap, self.log, self.global_cap = inner, connect, cap, log, global_cap
        self.stopped, self.stop_reason = False, ""

    def _refusal(self, con):
        if self.global_cap is not None:
            if db.global_killed(con):
                return "global_kill"
            if db.global_calls_today(con) >= self.global_cap:
                return "global_cap"
        if db.get_setting(con, "kill") == "1":
            return "kill_switch"
        return "daily_cap" if db.calls_today(con) >= self.cap else ""

    def complete(self, *args, **kwargs):
        con = self.connect()
        try:
            reason = self._refusal(con)
            if reason:
                self.stopped, self.stop_reason = True, reason
                db.add_usage(con, "refused", reason)
                self.log("model_refused", None, reason=reason, cap=self.cap)
                raise Refused(reason)
            db.add_usage(con, "call")
            self.log("model_call", None, used_today=db.calls_today(con), cap=self.cap)
        finally:
            con.close()
        inner = self.inner() if callable(self.inner) else self.inner
        out = inner.complete(*args, **kwargs)
        used = getattr(inner, "last_usage", None)                   # (model, tokens_in, tokens_out) when the backend reports it
        if used:
            con = self.connect()
            try:
                db.add_usage(con, "tokens", "", *used)
            finally:
                con.close()
        return out


def default_cap(config_path="config.yaml"):
    try:
        with open(config_path, encoding="utf-8") as f:
            return int((yaml.safe_load(f) or {}).get("daily_call_cap", DEFAULT_CAP))
    except OSError:
        return DEFAULT_CAP


def capped_default(db_path, log):
    """The real backend (BACKEND=api|claude-code), created on first use, behind the cap and kill switch."""
    return CappedBackend(get_backend, db_path, default_cap(), log)
