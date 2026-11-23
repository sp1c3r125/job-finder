"""Research source: for each target title, one claude-code call with ONLY web search and web fetch turned on.
The model proposes postings; plain code checks every one before it is saved. A URL from the model is never saved
unchecked: it must return HTTP 200 without a login, the page must contain the job title, and the domain must
not be an aggregator. Failures are dropped and logged."""
import json
import re
from datetime import datetime, timezone
from urllib.parse import urljoin, urlparse

import requests

from ..checks import check_shape, norm
from ..models import Job
from ..text import clean_text, html_to_text, to_iso

TOOLS = "WebSearch,WebFetch"
# Job boards whose terms ban bots, scraping or aggregation. A domain stays here until its terms are checked.
# Each entry matches the domain and every subdomain, never a look-alike name (notjobstreet.com).
BLOCKED_DOMAINS = ("jobstreet.com", "jobsdb.com", "seek.com.au", "seek.co.nz", "linkedin.com", "indeed.com",
                   "glassdoor.com", "ziprecruiter.com", "foundit.in", "remote.co", "wellfound.com", "workatastartup.com",
                   "seek.com", "jobstreet.com.ph", "jobstreet.com.my", "jobstreet.com.sg", "jobstreet.co.id", "lnkd.in")
MAX_TITLES = 8
MAX_REDIRECTS = 5
_REDIRECT = (301, 302, 303, 307, 308)

_S = {"type": "string"}
_POSTING = {"type": "object", "properties": {"company": _S, "title": _S, "url": _S, "posted": _S, "location": _S},
            "required": ["company", "title", "url", "posted", "location"], "additionalProperties": False}
SCHEMA = {"type": "object", "properties": {"results": {"type": "array", "items": _POSTING}},
          "required": ["results"], "additionalProperties": False}

SYSTEM = """You find real, currently open job postings on the web, using web search and web fetch.
Find REMOTE postings for the job title given, posted in the last 7 days, that are open to candidates in the
Philippines, APAC or worldwide. Use the company's own careers page or its applicant-tracking page (Greenhouse,
Lever, Ashby, Workable and similar). Do not return links on these job boards or their subdomains: {BLOCKED}. Open each page you return and confirm it is a live posting for that role.
company: the employer. title: the posting's exact title. url: the direct link to the posting.
posted: the posting date as YYYY-MM-DD, or "" if the page shows none. location: the location text as the posting says it.
Return only postings you saw on a page. Never guess a URL or a date. Return an empty list if you find none.""".replace("{BLOCKED}", ", ".join(BLOCKED_DOMAINS))

_LOGIN_PATH = re.compile(r"(?i)/(log-?in|sign-?in|signup|sign-up|sso|auth|oauth)(/|$)")


def blocked(url):
    host = (urlparse(url).hostname or "").lower().rstrip(".")
    return next((d for d in BLOCKED_DOMAINS if host == d or host.endswith("." + d)), None)


def default_get(url, user_agent):
    """One request, no redirects: check_page follows them by hand so a blocked host is never contacted."""
    return requests.get(url, headers={"User-Agent": user_agent}, timeout=30, allow_redirects=False)


_LDJSON = re.compile(r"(?is)<script[^>]+type=[\"']?application/ld\+json[\"']?[^>]*>(.*?)</script>")


def _walk(x):
    if isinstance(x, list):
        for i in x:
            yield from _walk(i)
    elif isinstance(x, dict):
        yield x
        yield from _walk(x.get("@graph") or [])


def job_posting(body):
    """The first schema.org JobPosting in the page's ld+json blocks (also inside "@graph" or a list), or None."""
    for m in _LDJSON.finditer(body):
        try:
            data = json.loads(m.group(1).strip())
        except ValueError:
            continue
        for d in _walk(data):
            t = d.get("@type")
            if t == "JobPosting" or (isinstance(t, list) and "JobPosting" in t):
                return d
    return None


def check_link(result, get):
    """(page_text, "") if the posting link is good, else ("", reason). `get(url)` returns a response-like object."""
    info, why = check_page(result, get)
    return info["text"], why


def check_page(result, get):
    """({text, posted, expires}, "") if the posting link is good, else (empty info, reason).
    With a JobPosting block, its description, datePosted and validThrough are used; otherwise the cleaned page text."""
    none = {"text": "", "posted": "", "expires": ""}
    url = result["url"].strip()
    parts = urlparse(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return none, "not an http(s) link"
    d = blocked(url)
    if d:
        return none, f"blocked domain {d}"
    cur = url
    for hop in range(MAX_REDIRECTS + 1):
        try:
            resp = get(cur)
        except Exception as e:  # noqa: BLE001
            return none, f"request failed: {type(e).__name__}"
        loc = (getattr(resp, "headers", None) or {}).get("Location")
        if resp.status_code not in _REDIRECT or not loc:
            break
        if hop == MAX_REDIRECTS:
            return none, "too many redirects"
        cur = urljoin(cur, loc)
        d = blocked(cur)
        if d:                                                          # checked before the hop: no request is sent
            return none, f"redirects to blocked domain {d}"
        if urlparse(cur).scheme not in ("http", "https"):
            return none, "redirects to a non-http link"
    final = str(getattr(resp, "url", cur) or cur)
    d = blocked(final)
    if d:
        return none, f"redirects to blocked domain {d}"
    if resp.status_code != 200:
        return none, f"HTTP {resp.status_code}"
    if _LOGIN_PATH.search(urlparse(final).path or ""):
        return none, "redirects to a login page"
    body = resp.text or ""
    if re.search(r"(?i)<input[^>]+type=[\"']?password", body):
        return none, "page asks for a login"
    jp = job_posting(body)                                             # read before scripts are stripped
    want = f" {norm(result['title'])} "
    on_page = want in f" {norm(body)} "                                # raw HTML: the title may only be in <title>/meta
    if jp:
        on_page = on_page or want in f" {norm(str(jp.get('title') or ''))} "
    if not on_page:
        return none, "page does not contain the job title"
    if jp:
        text = clean_text(html_to_text(str(jp.get("description") or "")))
        if text:
            return {"text": text, "posted": to_iso(jp.get("datePosted")), "expires": to_iso(jp.get("validThrough"))}, ""
    text = clean_text(html_to_text(re.sub(r"(?is)<(script|style|head)[^>]*>.*?</\1>", " ", body)))
    return {"text": text, "posted": "", "expires": ""}, ""


def to_job(result, page_text, now=None, posted="", expires=""):
    """A page that shows no date (or one we cannot read) is kept with posted = the first-seen date and undated=True.
    A date the page does show is kept as it is, so research_max_age_days still applies to it."""
    posted = to_iso(posted) or (to_iso(result["posted"].strip()) if result["posted"].strip() else "")   # the page's own date wins
    undated = not posted
    if undated:
        posted = (now or datetime.now(timezone.utc)).isoformat()
    url = result["url"].strip()
    return Job("research", url, result["company"].strip(), result["title"].strip(), url, posted,
               result["location"].strip(), True, page_text[:12000], undated=undated, expires=to_iso(expires))


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
    """All verified research postings for the target titles. stats (a dict) gets 'returned', 'blocked' and 'link_ok' counts."""
    stats = stats if stats is not None else {}
    stats.update(returned=0, blocked=0, link_ok=0, finished=False)
    answered = stopped = 0
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
            stopped = 1
            break
        results = search_title(backend, title, log)
        if results is None:
            continue
        answered += 1
        stats["returned"] += len(results)
        for r in results:
            key = r["url"].strip()
            if key in seen:
                continue
            seen.add(key)
            d = blocked(key)
            if d:                                   # dropped before the link check: no request is ever sent
                stats["blocked"] += 1
                log("research_dropped", None, query=title, company=r["company"], title=r["title"], url=key, reason=f"blocked domain {d}")
                continue
            info, why = check_page(r, get)
            if why:
                log("research_dropped", None, query=title, company=r["company"], title=r["title"], url=key, reason=why)
                continue
            stats["link_ok"] += 1
            job = to_job(r, info["text"], posted=info["posted"], expires=info["expires"])
            log("research_link_ok", job, query=title)
            jobs.append(job)
    log("research_blocked", None, count=stats["blocked"])
    stats["finished"] = bool(answered) and not stopped     # a run the cap cut short, or where every call failed, may retry
    return jobs
