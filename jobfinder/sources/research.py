"""Research source: for each target title, one claude-code call with ONLY web search and web fetch turned on.
The model proposes postings; plain code checks every one before it is saved. A URL from the model is never saved
unchecked: it must return HTTP 200 without a login, the page must contain the job title, and the domain must
not be an aggregator. Failures are dropped and logged."""
import re
from datetime import datetime, timezone
from urllib.parse import urlparse

import requests

from ..checks import check_shape, norm
from ..models import Job
from ..text import html_to_text, to_iso

TOOLS = "WebSearch,WebFetch"
BLOCKED_DOMAINS = ("linkedin.com", "indeed.com", "glassdoor.com", "wellfound.com", "workatastartup.com", "remote.co")
MAX_TITLES = 8

_S = {"type": "string"}
_POSTING = {"type": "object", "properties": {"company": _S, "title": _S, "url": _S, "posted": _S, "location": _S},
            "required": ["company", "title", "url", "posted", "location"], "additionalProperties": False}
SCHEMA = {"type": "object", "properties": {"results": {"type": "array", "items": _POSTING}},
          "required": ["results"], "additionalProperties": False}

SYSTEM = """You find real, currently open job postings on the web, using web search and web fetch.
Find REMOTE postings for the job title given, posted in the last 7 days, that are open to candidates in the
Philippines, APAC or worldwide. Use the company's own careers page or its applicant-tracking page (Greenhouse,
Lever, Ashby, Workable and similar). Do not return LinkedIn, Indeed, Glassdoor, Wellfound, Work at a Startup or
Remote.co links. Open each page you return and confirm it is a live posting for that role.
company: the employer. title: the posting's exact title. url: the direct link to the posting.
posted: the posting date as YYYY-MM-DD, or "" if the page shows none. location: the location text as the posting says it.
Return only postings you saw on a page. Never guess a URL or a date. Return an empty list if you find none."""

_LOGIN_PATH = re.compile(r"(?i)/(log-?in|sign-?in|signup|sign-up|sso|auth|oauth)(/|$)")


def blocked(url):
    host = (urlparse(url).hostname or "").lower()
    return next((d for d in BLOCKED_DOMAINS if host == d or host.endswith("." + d)), None)


def default_get(url, user_agent):
    return requests.get(url, headers={"User-Agent": user_agent}, timeout=30, allow_redirects=True)


def check_link(result, get):
    """(page_text, "") if the posting link is good, else ("", reason). `get(url)` returns a response-like object."""
    url = result["url"].strip()
    parts = urlparse(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return "", "not an http(s) link"
    d = blocked(url)
    if d:
        return "", f"blocked domain {d}"
    try:
        resp = get(url)
    except Exception as e:  # noqa: BLE001
        return "", f"request failed: {type(e).__name__}"
    final = str(getattr(resp, "url", url) or url)
    d = blocked(final)
    if d:
        return "", f"redirects to blocked domain {d}"
    if resp.status_code != 200:
        return "", f"HTTP {resp.status_code}"
    if _LOGIN_PATH.search(urlparse(final).path or ""):
        return "", "redirects to a login page"
    body = resp.text or ""
    if re.search(r"(?i)<input[^>]+type=[\"']?password", body):
        return "", "page asks for a login"
    if f" {norm(result['title'])} " not in f" {norm(body)} ":       # raw HTML: the title may only be in <title>/meta
        return "", "page does not contain the job title"
    text = html_to_text(re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", body))
    return text, ""


def _to_job(result, page_text):
    posted = to_iso(result["posted"].strip()) if result["posted"].strip() else ""
    url = result["url"].strip()
    return Job("research", url, result["company"].strip(), result["title"].strip(), url, posted,
               result["location"].strip(), True, page_text[:12000])


def search_title(backend, title, log, today=None):
    """One model call for one target title. Returns the raw result list, or None if the call failed."""
    today = today or datetime.now(timezone.utc).date().isoformat()
    user = f"Today is {today}. Job title to search for: {title}"
    try:
        raw = check_shape(backend.complete(SYSTEM, user, schema=SCHEMA, tool_name="submit_research",
                                           tools=TOOLS, timeout=900), SCHEMA)
    except Exception as e:  # noqa: BLE001 - log every failure kind
        log("research_failed", None, title=title, error=f"{type(e).__name__}: {e}"[:300])
        return None
    return raw["results"]


def fetch(backend, cfg, log, get=None, stats=None):
    """All verified research postings for the target titles. stats (a dict) gets 'returned' and 'link_ok' counts."""
    stats = stats if stats is not None else {}
    stats.update(returned=0, link_ok=0)
    titles = ((cfg.get("target") or {}).get("titles") or [])[:MAX_TITLES]
    if not titles:
        log("research_skipped", None, reason="no target titles")
        return []
    ua = cfg.get("user_agent", "job-finder")
    get = get or (lambda url: default_get(url, ua))
    seen, jobs = set(), []
    for title in titles:
        if getattr(backend, "stopped", False):
            log("research_stopped", None, reason="model refused (kill switch or daily cap)")
            break
        results = search_title(backend, title, log)
        if results is None:
            continue
        stats["returned"] += len(results)
        for r in results:
            key = r["url"].strip()
            if key in seen:
                continue
            seen.add(key)
            text, why = check_link(r, get)
            if why:
                log("research_dropped", None, query=title, company=r["company"], title=r["title"], url=key, reason=why)
                continue
            stats["link_ok"] += 1
            job = _to_job(r, text)
            log("research_link_ok", job, query=title)
            jobs.append(job)
    return jobs
