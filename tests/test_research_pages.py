import json

from jobfinder import text as T
from jobfinder.sources import research
from .test_research import FakeGet, Resp, posting, GOOD_URL


def page(ld=None, body="<h1>Content Analyst</h1>"):
    block = f'<script type="application/ld+json">{json.dumps(ld)}</script>' if ld is not None else ""
    return f"<html><head><title>Content Analyst - Acme</title>{block}</head><body>{body}</body></html>"


def info(html, **kw):
    r = research.check_page(posting(title="Content Analyst", **kw), FakeGet({GOOD_URL: Resp(html)}))
    return r


JP = {"@type": "JobPosting", "title": "Content Analyst", "hiringOrganization": {"name": "Acme"},
      "description": "<p>Review articles for accuracy.</p><ul><li>Write summaries</li></ul>",
      "datePosted": "2026-11-18", "validThrough": "2026-12-31T00:00:00Z"}


def test_jobposting_block_gives_description_dates():
    i, why = info(page(JP, body="<nav>Menu %BUTTON_APPLY%</nav>"))
    assert why == ""
    assert i["text"] == "Review articles for accuracy.\nWrite summaries"
    assert i["posted"].startswith("2026-11-18") and i["expires"].startswith("2026-12-31")


def test_jobposting_inside_graph_and_list():
    g = {"@context": "https://schema.org", "@graph": [{"@type": "WebSite"}, JP]}
    assert info(page(g))[0]["text"].startswith("Review articles")
    assert info(page([{"@type": "Organization"}, JP]))[0]["posted"].startswith("2026-11-18")


def test_jobposting_title_alone_satisfies_title_check():
    html = '<html><script type="application/ld+json">%s</script><body>Welcome</body></html>' % json.dumps(JP)
    i, why = info(html)
    assert why == "" and i["text"]


def test_no_jobposting_uses_cleaned_page_text_and_no_dates():
    body = ('"><div>Content Analyst</div><div>%HEADER_COMPANY_WEBSITE%</div><div>Apply %BUTTON_APPLY_TO_POSITION%</div>'
            "<div>Content Analyst</div><p>Check facts. -</p><p>Check facts.</p>")
    i, why = info(page(None, body=body))
    assert why == "" and i["posted"] == "" and i["expires"] == ""
    assert "%" not in i["text"] and not i["text"].startswith('"')
    lines = i["text"].split("\n")
    assert lines.count("Content Analyst") == 1 and lines.count("Check facts.") == 1
    assert "Apply" in lines and "Acme" not in i["text"]


def test_bad_ld_json_is_ignored():
    html = '<html><title>Content Analyst</title><script type="application/ld+json">{oops</script><body>Hi</body></html>'
    i, why = info(html)
    assert why == "" and i["posted"] == ""


def test_clean_text():
    s = '">\n%A_B% \n\nHello -\nhello\n  World  : \n'
    assert T.clean_text(s) == "Hello\nWorld"
    assert T.clean_text(None) == ""


def test_page_date_replaces_undated():
    j = research.to_job(posting(posted=""), "x", posted="2026-11-18T00:00:00+00:00", expires="2026-12-31")
    assert not j.undated and j.posted.startswith("2026-11-18") and j.expires.startswith("2026-12-31")
