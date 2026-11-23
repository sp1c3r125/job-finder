import json
import shutil
from pathlib import Path

import pytest
import yaml

from jobfinder import main
from jobfinder.backends import ClaudeCodeBackend
from jobfinder.guard import CappedBackend
from jobfinder.sources import research
from .conftest import CFG, GOOD, TARGET, FakeBackend, recent

ROOT = Path(__file__).resolve().parent.parent
GOOD_URL = "https://jobs.ashbyhq.com/acme/1"
PAGE = "<html><head><title>AI Automation Engineer - Acme</title></head><body>Remote, open to APAC. Build LLM agents.</body></html>"


def posting(**kw):
    d = dict(company="Acme", title="AI Automation Engineer", url=GOOD_URL, posted="2026-11-20", location="Remote - APAC")
    d.update(kw)
    return d


class Resp:
    def __init__(self, text=PAGE, status=200, url=None, location=None):
        self.text, self.status_code, self.url = text, status, url
        self.headers = {"Location": location} if location else {}


class FakeGet:
    """Mocked HTTP: url -> Resp (or an exception). Records every url asked for."""
    def __init__(self, pages):
        self.pages, self.calls = pages, []

    def __call__(self, url):
        self.calls.append(url)
        r = self.pages[url]
        if isinstance(r, Exception):
            raise r
        return r


# --- the link check ---------------------------------------------------------------------------------------------

def test_good_link_returns_page_text():
    text, why = research.check_link(posting(), FakeGet({GOOD_URL: Resp()}))
    assert why == "" and "Build LLM agents" in text and "<" not in text


@pytest.mark.parametrize("pages, kw, reason", [
    ({GOOD_URL: Resp(status=404)}, {}, "HTTP 404"),
    ({GOOD_URL: Resp(status=403)}, {}, "HTTP 403"),
    ({GOOD_URL: Resp(url="https://acme.com/login?next=/jobs/1")}, {}, "redirects to a login page"),
    ({GOOD_URL: Resp(text="<form><input type='password' name='p'></form> AI Automation Engineer")}, {}, "page asks for a login"),
    ({GOOD_URL: Resp(text="<html>Careers at Acme</html>")}, {}, "page does not contain the job title"),
    ({GOOD_URL: Resp(url="https://www.linkedin.com/jobs/view/1")}, {}, "redirects to blocked domain linkedin.com"),
    ({GOOD_URL: ConnectionError("boom")}, {}, "request failed: ConnectionError"),
    ({}, {"url": "ftp://acme.com/1"}, "not an http(s) link"),
    ({}, {"url": "not a url"}, "not an http(s) link"),
])
def test_bad_links_are_dropped_with_a_reason(pages, kw, reason):
    text, why = research.check_link(posting(**kw), FakeGet(pages))
    assert (text, why) == ("", reason)


@pytest.mark.parametrize("url", [
    "https://www.linkedin.com/jobs/view/1", "https://uk.indeed.com/viewjob?jk=1", "https://glassdoor.com/job/1",
    "https://wellfound.com/jobs/1", "https://www.workatastartup.com/jobs/1", "https://remote.co/job/1",
    "https://jobs.remote.co/x"])
def test_blocked_domains_are_never_requested(url):
    get = FakeGet({})
    text, why = research.check_link(posting(url=url), get)
    assert why.startswith("blocked domain") and get.calls == []


@pytest.mark.parametrize("url,is_blocked", [
    ("https://ph.jobstreet.com/job/123", True), ("https://boards.greenhouse.io/acme/jobs/1", False),
    ("https://notjobstreet.com/x", False), ("https://jobstreet.com/x", True), ("https://www.seek.com.au/job/1", True),
    ("https://nz.seek.co.nz/job/1", True), ("https://ph.jobsdb.com/job/1", True), ("https://www.ziprecruiter.com/jobs/1", True),
    ("https://www.foundit.in/job/1", True), ("https://jobstreet.com.evil.example/x", False), ("https://employer.seek.com/x", True), ("https://seek.com/x", True),
    ("https://myseek.com/x", False), ("https://www.jobstreet.com.ph/en/job/1", True), ("https://my.jobstreet.com.my/job/1", True),
    ("https://sg.jobstreet.com.sg/job/1", True), ("https://id.jobstreet.co.id/job/1", True), ("https://lnkd.in/abc", True),
    ("https://notlnkd.in/abc", False)])
def test_blocked_matches_domain_and_subdomains_only(url, is_blocked):
    assert bool(research.blocked(url)) is is_blocked


def test_fetch_counts_and_logs_blocked_results_and_sends_no_request_to_them(logs):
    events, log = logs
    cfg = dict(CFG, target=dict(TARGET, titles=["A"]))
    b = FakeBackend([{"results": [posting(url="https://ph.jobstreet.com/job/123"), posting(url="https://www.seek.com.au/job/9"),
                                  posting()]}])
    get = FakeGet({GOOD_URL: Resp()})
    stats = {}
    jobs = research.fetch(b, cfg, log, get=get, stats=stats)
    assert [j.url for j in jobs] == [GOOD_URL] and stats["blocked"] == 2 and get.calls == [GOOD_URL]
    assert [f["count"] for e, f in events if e == "research_blocked"] == [2]


def test_redirect_to_blocked_domain_is_not_requested():
    get = FakeGet({GOOD_URL: Resp(status=302, location="https://www.linkedin.com/jobs/1")})
    text, why = research.check_link(posting(), get)
    assert (text, why) == ("", "redirects to blocked domain linkedin.com") and get.calls == [GOOD_URL]


def test_redirects_between_allowed_hosts_are_followed_by_hand():
    u = [f"https://h{i}.example.com/p" for i in range(4)]
    get = FakeGet({GOOD_URL: Resp(status=301, location=u[0]), u[0]: Resp(status=302, location="/next"),
                   "https://h0.example.com/next": Resp(status=307, location=u[2]), u[2]: Resp()})
    text, why = research.check_link(posting(), get)
    assert why == "" and "Build LLM agents" in text and len(get.calls) == 4


def test_too_many_redirects_are_dropped():
    pages = {GOOD_URL: Resp(status=302, location="https://h1.example.com/")}
    pages.update({f"https://h{i}.example.com/": Resp(status=302, location=f"https://h{i + 1}.example.com/") for i in range(1, 7)})
    get = FakeGet(pages)
    assert research.check_link(posting(), get) == ("", "too many redirects") and len(get.calls) == 6


def test_five_redirects_are_still_allowed():
    pages = {GOOD_URL: Resp(status=302, location="https://h1.example.com/")}
    pages.update({f"https://h{i}.example.com/": Resp(status=302, location=f"https://h{i + 1}.example.com/") for i in range(1, 5)})
    pages["https://h5.example.com/"] = Resp()
    assert research.check_link(posting(), FakeGet(pages))[1] == ""


def test_lookalike_domain_is_not_blocked():
    assert research.blocked("https://notlinkedin.com/x") is None and research.blocked("https://remote.company.com/x") is None


# --- fetch: one call per title, only web tools, nothing unchecked is kept ----------------------------------------

def test_one_call_per_title_web_tools_only_and_only_checked_urls_kept(logs):
    events, log = logs
    cfg = dict(CFG, target=dict(TARGET, titles=["A", "B"]))
    b = FakeBackend([{"results": [posting(), posting(url="https://x.com/404", title="Other")]},
                     {"results": [posting(),                                                        # same URL again
                                  posting(url="https://www.linkedin.com/jobs/view/9")]}])
    get = FakeGet({GOOD_URL: Resp(), "https://x.com/404": Resp(status=404)})
    stats = {}
    jobs = research.fetch(b, cfg, log, get=get, stats=stats)
    assert [j.url for j in jobs] == [GOOD_URL] and stats == {"returned": 4, "blocked": 1, "link_ok": 1, "finished": True}
    assert b.tools == [research.TOOLS, research.TOOLS] == ["WebSearch,WebFetch"] * 2 and len(b.calls) == 2
    assert "A" in b.calls[0][1] and "B" in b.calls[1][1]
    assert get.calls == [GOOD_URL, "https://x.com/404"]                 # the blocked link was not requested, the repeat not re-fetched
    dropped = [f for e, f in events if e == "research_dropped"]
    assert sorted(d["reason"] for d in dropped) == ["HTTP 404", "blocked domain linkedin.com"]
    j = jobs[0]
    assert (j.source, j.company, j.remote, j.location) == ("research", "Acme", True, "Remote - APAC")
    assert j.posted.startswith("2026-11-20") and "Build LLM agents" in j.description


def test_a_failed_call_is_logged_and_the_next_title_still_runs(logs):
    events, log = logs
    cfg = dict(CFG, target=dict(TARGET, titles=["A", "B"]))
    b = FakeBackend([RuntimeError("claude exited 1"), {"wrong": "shape"}])
    assert research.fetch(b, cfg, log, get=FakeGet({})) == []
    assert [e for e, _ in events].count("research_failed") == 2


def test_no_target_titles_means_no_calls(logs):
    events, log = logs
    b = FakeBackend([])
    assert research.fetch(b, CFG, log, get=FakeGet({})) == [] and b.calls == []
    assert events[-1][0] == "research_skipped"


def test_at_most_8_titles():
    b = FakeBackend([{"results": []}] * 12)
    research.fetch(b, dict(CFG, target=dict(TARGET, titles=[f"T{i}" for i in range(12)])), lambda *a, **k: None, get=FakeGet({}))
    assert len(b.calls) == 8


# --- the daily cap --------------------------------------------------------------------------------------------

def test_research_calls_count_against_the_daily_cap_and_stop_at_it(tmp_path, logs):
    events, log = logs
    inner = FakeBackend([{"results": []}] * 8)
    capped = CappedBackend(inner, str(tmp_path / "t.db"), 2, log, kill_file=str(tmp_path / "KILL"))
    research.fetch(capped, dict(CFG, target=dict(TARGET, titles=["A", "B", "C", "D"])), log, get=FakeGet({}))
    assert len(inner.calls) == 2                                          # cap 2: the third call was refused, the rest skipped
    assert "research_stopped" in [e for e, _ in events] and capped.stopped


# --- the claude-code backend: exactly the web tools -------------------------------------------------------------

def test_claude_code_backend_enables_only_web_tools_for_research():
    seen = []

    def run(cmd, **kw):
        seen.append((cmd, kw))
        return type("P", (), {"returncode": 0, "stdout": json.dumps({"structured_output": {"results": []}}), "stderr": ""})()

    be = ClaudeCodeBackend(run=run)
    be.complete("s", "u", schema=research.SCHEMA, tools=research.TOOLS, timeout=900)
    be.complete("s", "u")
    web, plain = seen
    assert web[0][web[0].index("--tools") + 1] == "WebSearch,WebFetch" and web[1]["timeout"] == 900
    assert web[0][web[0].index("--allowedTools") + 1] == "WebSearch,WebFetch"
    assert plain[0][plain[0].index("--tools") + 1] == "" and "--allowedTools" not in plain[0]


# --- the daily run: research goes through the same pre-filter, scoring and drafts --------------------------------

def run_in(tmp_path, monkeypatch, research_on, backend, get, extra_target=None):
    monkeypatch.chdir(tmp_path)
    cfg = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
    cfg.update(research=research_on, aggregators=[], tailor_resume=False)
    Path("config.yaml").write_text(yaml.safe_dump(cfg), encoding="utf-8")
    shutil.copy(ROOT / "profile.example.yaml", "profile.yaml")
    Path("companies.yaml").write_text("companies: []\n")
    Path("target.yaml").write_text(yaml.safe_dump(dict(TARGET, titles=["AI Automation Engineer"])), encoding="utf-8")
    Path("resume.json").write_text(json.dumps({"name": "Alex", "skills": ["n8n"]}), encoding="utf-8")
    return main.run(["--db", "t.db"], backend=backend, http=object(), research_get=get)


def test_run_research_results_are_prefiltered_and_scored(tmp_path, monkeypatch, capsys):
    recent_day = recent(2)[:10]
    results = [posting(posted=recent_day),
               posting(url="https://acme.com/2", title="Frontend Developer", posted=recent_day),     # title check rejects
               posting(url="https://acme.com/3", title="AI Trainer", posted=recent_day),             # exclude word rejects
               posting(url="https://acme.com/4", posted="2026-01-01"),                               # too old for 7 days
               posting(url="https://acme.com/5", title="AI Engineer", posted=recent_day, location="Remote - US only")]
    pages = {r["url"]: Resp(text=f"<title>{r['title']}</title> Remote role.") for r in results}
    b = FakeBackend([{"results": results}, GOOD])
    assert run_in(tmp_path, monkeypatch, True, b, FakeGet(pages)) == 0
    out = capsys.readouterr().out
    assert "research: 5 results returned, 5 passed the link check" in out
    assert "research: 1 passed the pre-filter, 1 scored" in out
    assert b.tools[0] == research.TOOLS and b.tools[1:] == [None]          # scoring has no tools; no cover note is drafted by the run
    import sqlite3
    con = sqlite3.connect("t.db")
    assert con.execute("select j.source, j.url, u.passed from jobs j join user_jobs u on u.source=j.source and u.source_id=j.source_id where u.passed=1").fetchall() == [("research", GOOD_URL, 1)]
    assert con.execute("select count(*) from job_scores where score_json is not null").fetchone()[0] == 1
    assert not Path("drafts").exists() or not list(Path("drafts").glob("*.md"))


def test_research_switch_off_makes_no_research_call(tmp_path, monkeypatch):
    b = FakeBackend([])
    assert run_in(tmp_path, monkeypatch, False, b, FakeGet({})) == 0
    assert b.calls == []


# --- undated results are kept with the first-seen date -------------------------------------------------------------

def test_an_undated_result_gets_the_first_seen_date_and_a_dated_one_keeps_its_own():
    from datetime import datetime, timezone
    now = datetime(2026, 11, 23, 4, 0, tzinfo=timezone.utc)
    u = research.to_job(posting(posted=""), "page", now)
    assert u.undated and u.posted == now.isoformat()
    assert research.to_job(posting(posted="not a date"), "page", now).undated      # unreadable counts as not shown
    d = research.to_job(posting(posted="2026-11-20"), "page", now)
    assert not d.undated and d.posted.startswith("2026-11-20")


def test_run_keeps_an_undated_result_but_still_rejects_an_old_dated_one(tmp_path, monkeypatch, capsys):
    import sqlite3
    from jobfinder import db, report
    results = [posting(posted=""), posting(url="https://acme.com/old", posted="2026-01-01")]
    pages = {r["url"]: Resp() for r in results}
    b = FakeBackend([{"results": results}, GOOD])
    assert run_in(tmp_path, monkeypatch, True, b, FakeGet(pages)) == 0
    con = sqlite3.connect("t.db")
    assert con.execute("select j.url, u.passed, j.undated from jobs j join user_jobs u on u.source=j.source and u.source_id=j.source_id order by j.url").fetchall() == [
        ("https://acme.com/old", 0, 0), (GOOD_URL, 1, 1)]
    row = report.rows(db.connect("t.db"))[0]
    assert row["posted"] == "date not shown" and row["score"] == GOOD["fit_score"]


def test_requalify_updates_a_saved_job(tmp_path):
    from jobfinder import db
    con = db.connect(str(tmp_path / "t.db"))
    job = research.to_job(posting(posted=""), "page")
    job.posted = ""
    db.save(con, job, "2026-11-22T00:00:00+00:00", False, "no posted date")
    db.requalify(con, job, "2026-11-22T00:00:00+00:00", True, True, "philippines")
    assert con.execute("select j.posted, j.undated, u.passed, u.rule from jobs j join user_jobs u on u.source=j.source and u.source_id=j.source_id").fetchone() == (
        "2026-11-22T00:00:00+00:00", 1, 1, "philippines")


# --- research runs once every research_every_days (default 7) -----------------------------------------------------------------------

def test_research_runs_once_a_day_and_a_cut_short_run_can_retry(tmp_path, monkeypatch, capsys):
    import sqlite3
    from datetime import datetime, timezone
    day = datetime.now(timezone.utc).date().isoformat()
    b = FakeBackend([{"results": []}])                       # one target title -> one research call
    assert run_in(tmp_path, monkeypatch, True, b, FakeGet({})) == 0
    assert len(b.calls) == 1
    assert sqlite3.connect("t.db").execute("select value from settings where key='research_last_run'").fetchone() == (day,)
    b2 = FakeBackend([])                                     # the same day again: no research call
    assert main.run(["--db", "t.db"], backend=b2, http=object(), research_get=FakeGet({})) == 0
    assert b2.calls == [] and "research: skipped" in capsys.readouterr().out
    from datetime import timedelta
    for days_ago, runs in ((6, False), (8, True)):           # weekly: 6 days ago is too soon, 8 days ago runs again
        d = (datetime.now(timezone.utc).date() - timedelta(days=days_ago)).isoformat()
        sqlite3.connect("t.db").execute("update settings set value=? where key='research_last_run'", (d,)).connection.commit()
        b3 = FakeBackend([{"results": []}])
        main.run(["--db", "t.db"], backend=b3, http=object(), research_get=FakeGet({}))
        assert len(b3.calls) == (1 if runs else 0), days_ago


def test_a_run_where_every_call_failed_is_not_marked_finished(tmp_path, monkeypatch):
    import sqlite3
    b = FakeBackend([RuntimeError("claude exited 1")])
    assert run_in(tmp_path, monkeypatch, True, b, FakeGet({})) == 0
    assert sqlite3.connect("t.db").execute("select count(*) from settings where key='research_last_run'").fetchone() == (0,)


def test_search_prompt_lists_every_blocked_domain():
    assert all(d in research.SYSTEM for d in research.BLOCKED_DOMAINS) and "{BLOCKED}" not in research.SYSTEM
