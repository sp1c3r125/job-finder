"""Rule score: 0-100, plain code, no model. Decides which jobs are worth a model call.
Parts (weights in config `rule_weights`): title match, resume skills found in the job text, years asked versus
years you have, remote and location. A deal-breaker found by regex caps the score so the job is not sent to the model.
Every score keeps its reasons (shown on the job page)."""
import re

import yaml

from .checks import norm
from .prefilter import _has, location_rule

WEIGHTS = {"title": 40, "skills": 30, "years": 15, "location": 15}
DEAL_BREAKER_CAP = 35            # below the default rule_min, so a job with a deal-breaker never goes to the model
CASE_SENSITIVE = {"make"}        # skills that are also everyday words: match the capitalised form only
_STOP = {"and", "or", "the", "of", "for", "a", "an", "in", "to", "with"}

_NEG = re.compile(r"(?i)\b(no|not|non|without|never|nor|regardless of|irrespective of|doesn'?t|don'?t)\b[^.\n]{0,25}$")
_US_ONLY = [
    r"\b(?:us|u\.s\.|usa|united states)[- ](?:only|based only|residents only)\b",
    r"\bmust (?:be )?(?:located|based|living|residing|reside) in the (?:us|u\.s\.|usa|united states)\b",
    r"\b(?:authori[sz]ed|eligible|legally (?:entitled|permitted)|right) to work in the (?:us|u\.s\.|usa|united states)\b",
    r"\b(?:only|restricted to) (?:candidates|applicants)? ?(?:located |based )?in the (?:us|u\.s\.|usa|united states)\b",
]
_CITIZEN = [r"\b(?:u\.?s\.?|united states|canadian|british|uk|eu) citizen(?:ship)?s?\b",
            r"\bcitizenship (?:is )?required\b", r"\bmust be a (?:\w+ )?citizen\b"]
_CLEARANCE = [r"\bsecurity clearance\b", r"\b(?:secret|top secret|ts/sci) clearance\b", r"\bclearance (?:is )?required\b"]
_ONSITE = re.compile(r"(?i)\b(?:on-?site|in[- ]office|in[- ]person)\b")
_REQ = re.compile(r"(?i)\b(required|requirements?|must|mandatory|essential|proficien\w+|strong|expert\w*|experience (?:with|in)|"
                  r"\d+\+? ?years?)\b")
_SOFT = re.compile(r"(?i)\b(nice to have|nice-to-have|bonus|preferred|a plus|desirable|familiar\w*|optional|not required)\b")
_YEARS = re.compile(r"(?i)\b(\d{1,2})\s*\+?\s*(?:(?:-|–|to)\s*(\d{1,2})\s*\+?\s*)?(?:years?|yrs?)\b")
_YEARS_CUE = re.compile(r"(?i)experience|\bexp\b|minimum|at least|required|background|track record")
_YEARS_NOT = re.compile(r"(?i)^\W*(?:old|ago|in business|of history|of combined|since)")


def load_facts(path="profile.yaml"):
    """{years_total, years_ai, known_gaps} from profile.yaml. The years are numbers (years_total, years_ai_automation);
    a number in years_experience is the fallback. The sentence form of years_experience is for the model only."""
    try:
        with open(path, encoding="utf-8") as f:
            p = yaml.safe_load(f) or {}
    except (OSError, yaml.YAMLError):
        p = {}
    total, ai = _num(p.get("years_total")), _num(p.get("years_ai_automation"))
    if total is None:                                          # older profile: years_experience as a number
        total = _num(p.get("years_experience"))
    if ai is None:
        ai = total
    gaps = [str(g) for g in (p.get("known_gaps") or []) if str(g).strip()]
    return {"years_total": total, "years_ai": ai, "known_gaps": gaps}


def _num(v):
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def _has_skill(skill, text):
    if skill.lower() in CASE_SENSITIVE:
        return re.search(r"(?<![\w])" + re.escape(skill) + r"(?![\w])", text) is not None
    return _has(skill, text)


def _search(patterns, text):
    """The first match not preceded by a negation such as 'no' or 'regardless of', else None."""
    for p in patterns:
        for m in re.finditer(p, text, re.I):
            if not _NEG.search(text[max(0, m.start() - 40):m.start()]):
                return m.group(0)
    return None


def title_strength(title, target):
    """0-1: exact target title 1.0; target title inside the job title 0.9; else shared words with a target title
    (up to 0.8). A title that only has a title word (the pre-filter's own rule) gets at least 0.5."""
    jt = norm(title)
    jw = {w for w in jt.split() if w not in _STOP}
    best, why = 0.0, ""
    for t in target.get("titles") or []:
        tn = norm(t)
        tw = [w for w in tn.split() if w not in _STOP]
        if not tw:
            continue
        if jt == tn:
            return 1.0, f"exactly your target title '{t}'"
        if f" {tn} " in f" {jt} ":
            s = 0.9
        else:
            s = 0.8 * sum(w in jw for w in tw) / len(tw)
        if s > best:
            best, why = s, f"close to your target title '{t}'"
    if any(re.search(r"(?<![\w])" + re.escape(w) + r"(?:s|es)?(?![\w])", title, re.I) for w in target.get("title_words") or []):
        if best < 0.5:
            best, why = 0.5, "has a target title word"
    return best, why or "no target title match"


_AI_FIELD = re.compile(r"(?i)(?<![\w])(AI|ML|LLMs?|automation|agents?|agentic|software|engineering|machine learning)(?![\w])")


def years_asked(text):
    """The first 'N+ years' / 'N-M years' that is about experience: (lower number, in_ai_field), else None.
    in_ai_field: the text within 80 characters names AI, ML, LLM, automation, agents, software or engineering."""
    for m in _YEARS.finditer(text):
        window = text[max(0, m.start() - 60):m.end() + 80]
        if not _YEARS_CUE.search(window) or _YEARS_NOT.search(text[m.end():m.end() + 25]):
            continue
        near = text[max(0, m.start() - 80):m.end() + 80]
        return int(m.group(1)), _AI_FIELD.search(near) is not None
    return None


_LEVELS = ["junior", "mid", "senior", "lead"]
_TITLE_LEVEL = [(re.compile(r"(?i)\b(junior|jr|associate|entry|intern|trainee|graduate)\b"), 0),
                (re.compile(r"(?i)\b(senior|sr)\b"), 2),
                (re.compile(r"(?i)\b(lead|staff|principal|head|director|founding|manager|vp)\b"), 3)]


def level_factor(title, level):
    """1.0 when the title's level is the one in your search profile or not stated; 0.8 one step away, 0.6 two or more."""
    if level not in _LEVELS:
        return 1.0, ""
    for pat, idx in _TITLE_LEVEL:
        if pat.search(title):
            gap = abs(idx - _LEVELS.index(level))
            return (1.0, "") if gap == 0 else (0.8 if gap == 1 else 0.6, f", level {_LEVELS[idx]} vs your {level}")
    return 1.0, ""


def deal_breakers(text, known_gaps, title=""):
    out = []
    if _search(_US_ONLY, text):
        out.append("US only")
    if _search(_CITIZEN, text):
        out.append("citizenship required")
    if _search(_CLEARANCE, text):
        out.append("security clearance")
    for m in _ONSITE.finditer(text):
        around = text[max(0, m.start() - 40):m.end() + 40]
        if not re.search(r"(?i)remote|hybrid|flexible", around) and not _NEG.search(text[max(0, m.start() - 40):m.start()]):
            out.append("on-site")
            break
    for gap in known_gaps:
        if _has(gap, title):                                 # a stack named in the title is the job's main stack
            out.append(f"{gap} required")
            continue
        for line in re.split(r"\n+|(?<=[.;!?])\s+", text):
            if _has(gap, line) and _REQ.search(line) and not _SOFT.search(line):
                out.append(f"{gap} required")
                break
    return out


def rule_score(job, cfg, res, facts):
    """{score, parts, reasons, deal_breakers}. res: resume.json dict or None. facts: load_facts()."""
    w = {**WEIGHTS, **(cfg.get("rule_weights") or {})}
    text = f"{job.title}\n{job.description}"
    parts, reasons = {}, []

    if cfg.get("target"):
        ts, why = title_strength(job.title, cfg["target"])
    else:                                         # no resume, no search profile: the keywords stand in for the target
        ts, why = (0.5, "a keyword is in the title") if any(_has(k, job.title) for k in cfg.get("keywords") or [])             else (0.0, "no keyword in the title")
    lf, lwhy = level_factor(job.title, (cfg.get("target") or {}).get("level", ""))
    ts, why = ts * lf, why + lwhy
    parts["title"] = w["title"] * ts
    reasons.append(f"title {parts['title']:.0f}/{w['title']}: {why}")

    skills = (res or {}).get("skills") or []
    found = [s for s in skills if _has_skill(s, text)]
    share = len(found) / len(skills) if skills else 0.0
    full = float(cfg.get("skills_full_share", 0.25))
    parts["skills"] = w["skills"] * min(1.0, share / full) if skills else 0.0
    reasons.append(f"skills {parts['skills']:.0f}/{w['skills']}: {len(found)} of {len(skills)} resume skills in the text"
                   + (f" ({', '.join(found[:8])})" if found else ""))

    ask = years_asked(job.description)
    asked, ai_field = ask if ask else (None, False)
    have = facts.get("years_ai") if ai_field else facts.get("years_total")
    kind = "AI/automation" if ai_field else "total"
    if asked is None or have is None:
        ys, why = 0.8, "no years asked" if asked is None else "your years are not in profile.yaml"
    elif asked <= have:
        ys, why = 1.0, f"asks {asked}+ ({kind}), you have {have:g}"
    else:
        ys, why = max(0.0, 1 - 0.25 * (asked - have)), f"asks {asked}+ ({kind}), you have {have:g}"
    parts["years"] = w["years"] * ys
    reasons.append(f"years {parts['years']:.0f}/{w['years']}: {why}")

    rule = location_rule(job, cfg.get("location_allow") or []) or ""
    base = rule.replace(" (description)", "")
    ls = 1.0 if base in ("philippines", "ph") else 0.9 if base in ("apac", "asia", "asia-pacific") else 0.8
    if "(description)" in rule:
        ls = min(ls, 0.6)
    if job.remote is not True:
        ls *= 0.9
    parts["location"] = w["location"] * ls
    reasons.append(f"location {parts['location']:.0f}/{w['location']}: {rule or 'none'}"
                   + ("" if job.remote is True else ", remote inferred from the text"))

    live = {k: v for k, v in w.items() if k != "skills" or skills}     # no resume skills: judge on the other parts only
    total = sum(parts.values()) * sum(w.values()) / sum(live.values()) if live else 0.0
    total = total * 100 / sum(w.values())
    db = deal_breakers(text, facts.get("known_gaps") or [], job.title)
    if db:
        total = min(total, DEAL_BREAKER_CAP)
        reasons.append("deal-breakers: " + ", ".join(db))
    return {"score": max(0, min(100, round(total))), "parts": {k: round(v, 1) for k, v in parts.items()},
            "reasons": reasons, "deal_breakers": db}
