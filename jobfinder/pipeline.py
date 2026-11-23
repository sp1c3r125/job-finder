"""Steps of the search that local mode (main.py) and the hosted worker (worker.py) both use, so they cannot drift apart."""
from . import db


def model_gate(con, cfg, now, log, source_max_age=None):
    """Which jobs go to the model today. Every stored job that passed this user's pre-filter and has no score yet, best rule
    scores first: rule score >= rule_min, at most model_daily_jobs a day (UTC).
    Returns (waiting, good, quota, to_score, rule_min, daily)."""
    waiting = db.unscored_passed(con, now, cfg["max_age_days"], source_max_age)
    rule_min, daily = cfg.get("rule_min", 40), cfg.get("model_daily_jobs", 15)
    quota = max(0, daily - db.model_scored_today(con))
    good = sorted((j for j in waiting if (j.rule_score or 0) >= rule_min), key=lambda j: -(j.rule_score or 0))
    to_score = good[:quota]
    log("model_gate", None, waiting=len(waiting), rule_min=rule_min, above_rule_min=len(good), quota=quota,
        to_score=len(to_score))
    return waiting, good, quota, to_score, rule_min, daily
