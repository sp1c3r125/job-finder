"""Plain-code pre-filter. No AI. Returns (passed, rule_or_reason)."""
import re
from datetime import datetime, timedelta, timezone

from .target import title_check


def _has(term, text):
    flags = 0 if (len(term) <= 3 and term.isupper()) else re.IGNORECASE
    return re.search(r"(?<![\w])" + re.escape(term) + r"(?![\w])", text, flags) is not None


def location_rule(job, allow):
    """First allowed term found in the location text, else in the first 1500 chars of the description."""
    for term in allow:
        if _has(term, job.location):
            return term
    for term in allow:
        if _has(term, job.description[:1500]) and re.search(
            r"(?i)(work from|located|open to|hire).{0,40}" + re.escape(term), job.description[:1500]
        ):
            return f"{term} (description)"
    return None


def max_age_days(cfg, source):
    """Research postings are asked for over the last 7 days, so that source gets its own window (never shorter)."""
    if source == "research":
        return max(cfg["max_age_days"], cfg.get("research_max_age_days", 7))
    return cfg["max_age_days"]


def prefilter(job, cfg, now=None):
    now = now or datetime.now(timezone.utc)
    if not job.posted:
        return False, "no posted date"
    if datetime.fromisoformat(job.posted) < now - timedelta(days=max_age_days(cfg, job.source)):
        return False, "older than max_age_days"
    is_remote = job.remote if job.remote is not None else ("remote" in job.location.lower() or "remote" in job.title.lower())
    if not is_remote:
        return False, "not remote"
    rule = location_rule(job, cfg["location_allow"])
    if not rule:
        return False, "location not allowed"
    target = cfg.get("target")
    if target:                                  # the title decides; a keyword in the description is not enough
        ok, why = title_check(job.title, target)
        if not ok:
            return False, why
    else:                                       # no resume, no search profile: the old keyword rule
        text = f"{job.title}\n{job.description}"
        if not any(_has(k, text) for k in cfg["keywords"]):
            return False, "no keyword match"
    return True, rule
