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
    def __init__(self, text=PAGE, status=200, url=None):
        self.text, self.status_code, self.url = text, status, url


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
    assert [j.url for j in jobs] == [GOOD_URL] and stats == {"returned": 4, "link_ok": 1}
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


def test_run_research_results_are_prefiltered_scored_and_drafted(tmp_path, monkeypatch, capsys):
    recent_day = recent(2)[:10]
    results = [posting(posted=recent_day),
               posting(url="https://acme.com/2", title="Frontend Developer", posted=recent_day),     # title check rejects
               posting(url="https://acme.com/3", title="AI Trainer", posted=recent_day),             # exclude word rejects
               posting(url="https://acme.com/4", posted="2026-01-01"),                               # too old for 7 days
               posting(url="https://acme.com/5", title="AI Engineer", posted=recent_day, location="Remote - US only")]
    pages = {r["url"]: Resp(text=f"<title>{r['title']}</title> Remote role.") for r in results}
    b = FakeBackend([{"results": results}, GOOD, {"note": "I build n8n workflows."}])
    assert run_in(tmp_path, monkeypatch, True, b, FakeGet(pages)) == 0
    out = capsys.readouterr().out
    assert "research: 5 results returned, 5 passed the link check" in out
    assert "research: 1 passed the pre-filter, 1 scored" in out
    assert b.tools[0] == research.TOOLS and b.tools[1:] == [None, None]          # scoring and drafting have no tools
    import sqlite3
    con = sqlite3.connect("t.db")
    assert con.execute("select source, url, passed from jobs where passed=1").fetchall() == [("research", GOOD_URL, 1)]
    assert con.execute("select count(*) from job_scores where score_json is not null").fetchone()[0] == 1
    assert len(list(Path("drafts").glob("*.md"))) == 1


def test_research_switch_off_makes_no_research_call(tmp_path, monkeypatch):
    b = FakeBackend([])
    assert run_in(tmp_path, monkeypatch, False, b, FakeGet({})) == 0
    assert b.calls == []
