import time

from jobfinder.dedupe import dedupe
from jobfinder.prefilter import prefilter
from jobfinder.sources import himalayas, remoteok, wwr
from .conftest import CFG, FakeHttp, make_job, recent

NOW = int(time.time())


def him_job(guid, **kw):
    d = {"guid": guid, "title": "AI Automation Engineer", "companyName": "Acme", "applicationLink": guid,
         "locationRestrictions": [], "pubDate": NOW - 3600, "description": "<p>Build agents</p>"}
    d.update(kw)
    return d


def test_himalayas_params_dedupe_and_stop():
    cfg = dict(CFG, keywords=["AI", "agent"], himalayas={"country": "PH", "timezone": "UTC+8", "max_pages": 3})
    pages = {}

    class H(FakeHttp):
        def get_json(self, url, params=None):
            self.calls.append((url, params))
            return {"jobs": [him_job("https://himalayas.app/companies/acme/jobs/a")] if params["page"] == 1 else []}

    http = H({})
    jobs = himalayas.fetch(http, cfg)
    assert len(jobs) == 1                       # same guid under two keywords is kept once
    j = jobs[0]
    assert (j.source, j.url, j.location, j.remote, j.description) == (
        "himalayas", "https://himalayas.app/companies/acme/jobs/a", "Worldwide", True, "Build agents")
    p = http.calls[0][1]
    assert (p["q"], p["sort"], p["country"], p["worldwide"], p["timezone"]) == ("AI", "recent", "PH", "true", "UTC+8")
    assert [c[1]["page"] for c in http.calls] == [1, 2, 1, 2]    # empty page ends each keyword


def test_himalayas_restricted_location_and_old_page_stops():
    cfg = dict(CFG, keywords=["AI"], himalayas={})
    old = him_job("old", pubDate=NOW - 30 * 86400, locationRestrictions=[{"alpha2": "PH", "name": "Philippines", "slug": "philippines"}])

    class H(FakeHttp):
        def get_json(self, url, params=None):
            self.calls.append((url, params))
            return {"jobs": [old]}

    http = H({})
    jobs = himalayas.fetch(http, cfg)
    assert jobs[0].location == "Philippines" and len(http.calls) == 1   # all-old page: no page 2


def test_himalayas_429_goes_through_http_client_backoff():
    # Backoff lives in HttpClient (tests/test_http.py); here we only check a 429 error surfaces, not swallowed.
    class Boom(FakeHttp):
        def get_json(self, url, params=None):
            raise RuntimeError("429")

    try:
        himalayas.fetch(Boom({}), dict(CFG, himalayas={}))
        assert False
    except RuntimeError:
        pass


def test_remoteok_skips_and_logs_terms_and_is_strict():
    terms = {"last_updated": 1, "legal": "Please link back to Remote OK"}
    def ok(id, loc, pos="AI Agent Engineer"):
        return {"id": id, "date": recent(), "company": "Acme", "position": pos, "location": loc,
                "description": "<p>LLM agents</p>", "url": f"https://remoteOK.com/remote-jobs/{id}"}
    http = FakeHttp({remoteok.URL: [terms, ok("1", "Worldwide"), ok("2", "US Only"), ok("3", ""),
                                    ok("4", "Asia"), ok("5", "Worldwide", "AI Engineer (Hybrid)")]})
    events = []
    jobs = remoteok.fetch(http, CFG, lambda e, j=None, **f: events.append((e, f)))
    assert len(jobs) == 5 and events[0][0] == "remoteok_terms" and "link back" in events[0][1]["legal"]
    passed = [j.source_id for j in jobs if prefilter(j, CFG)[0]]
    assert passed == ["1", "4"]                      # US Only, empty and hybrid all rejected
    assert jobs[0].url == "https://remoteOK.com/remote-jobs/1"


RSS = """<?xml version="1.0"?><rss version="2.0"><channel>
<item><title>Acme: AI Engineer</title><region>Anywhere in the World</region>
<description>&lt;p&gt;Build &lt;b&gt;LLM&lt;/b&gt; agents&lt;/p&gt;</description>
<pubDate>{date}</pubDate><guid>https://weworkremotely.com/remote-jobs/acme-ai</guid>
<link>https://weworkremotely.com/remote-jobs/acme-ai</link></item>
<item><title>Beta: Support Rep</title><region>USA Only</region><description>x</description>
<pubDate>{date}</pubDate><guid>g2</guid><link>https://weworkremotely.com/remote-jobs/beta</link></item>
</channel></rss>"""


def test_wwr_two_feeds_parse_and_dedupe_links():
    from email.utils import format_datetime
    from datetime import datetime, timezone
    xml = RSS.format(date=format_datetime(datetime.now(timezone.utc)))
    http = FakeHttp({f: xml for f in wwr.FEEDS})
    jobs = wwr.fetch(http, CFG)
    assert [c[0] for c in http.calls] == wwr.FEEDS
    assert len(jobs) == 2                                # same links in both feeds kept once
    j = jobs[0]
    assert (j.company, j.title, j.location, j.description) == ("Acme", "AI Engineer", "Anywhere in the World", "Build LLM agents")
    assert prefilter(j, CFG) == (True, "anywhere")
    assert not prefilter(jobs[1], CFG)[0]


def test_dedupe_keeps_earliest_posted_and_lists_others():
    a = make_job(source="wwr", source_id="w", company="Acme Inc.", title="AI Engineer", posted="2026-11-20T10:00:00+00:00")
    b = make_job(source="greenhouse", source_id="g", company="Acme Inc", title="AI  Engineer", posted="2026-11-19T10:00:00+00:00")
    c = make_job(source="remoteok", source_id="r", company="Acme Inc", title="ai engineer", posted="2026-11-19T10:00:00+00:00")
    d = make_job(source="ashby", source_id="x", company="Other", title="AI Engineer")
    out = dedupe([a, c, b, d])
    assert len(out) == 2
    kept = [j for j in out if j.company.startswith("Acme")][0]
    assert kept.source == "greenhouse" and sorted(kept.also_seen_on) == ["remoteok", "wwr"]


def test_dedupe_does_not_merge_same_source_postings():
    a = make_job(source="ashby", source_id="1", title="AI Engineer", location="Remote - Asia")
    b = make_job(source="ashby", source_id="2", title="AI Engineer", location="Remote - Worldwide")
    assert len(dedupe([a, b])) == 2
