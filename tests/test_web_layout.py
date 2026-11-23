"""Dashboard and job-page layout: details section, lists, hidden empty sections, times in the profile timezone."""
import json

from fastapi.testclient import TestClient

from jobfinder import db, web
from jobfinder.localtime import parse_zone, time_html
from .conftest import CFG, GOOD, make_job

POSTED = "2026-11-21T20:30:00+00:00"          # 04:30 on the 22nd in UTC+8


def make(tmp_path, profile=True):
    path = str(tmp_path / "t.db")
    con = db.connect(path)
    m = make_job(source_id="1", title="Model Job", posted=POSTED)
    db.save(con, m, POSTED, True, "worldwide")
    db.set_score(con, m, dict(GOOD, fit_score=80, matching_evidence=["Python", "n8n"], gaps=["Kubernetes", "Go"], deal_breakers=[]), None)
    r = make_job(source_id="2", title="Rule Job", posted=POSTED)
    db.save(con, r, POSTED, True, "worldwide")
    db.set_rule_scores(con, [(r, {"score": 55, "reasons": ["title matches"], "deal_breakers": []})])
    con.close()
    if profile:
        (tmp_path / "profile.yaml").write_text("name: Alex Example\ntimezone: UTC+8\n", encoding="utf-8")
    (tmp_path / "resume.json").write_text(json.dumps({"name": "Alex Example", "source_file": "abc.md",
        "imported_at": "2026-11-22T20:00:00+00:00", "roles": [{}], "skills": ["Python", "SQL"], "search_keywords": [], "left_out": []}), encoding="utf-8")
    app = web.create_app(path, dict(CFG), base_dir=tmp_path, log_path=str(tmp_path / "l.jsonl"))
    return TestClient(app, base_url="http://127.0.0.1")


def test_zone_parsing_and_time_element():
    assert parse_zone("UTC+8")[1] == "UTC+8" and parse_zone("+05:30")[0].utcoffset(None).seconds == 19800
    assert parse_zone("")[1] == "UTC" and parse_zone("nonsense")[1] == "UTC"
    z = parse_zone("UTC+8")
    assert time_html("2026-11-21T20:30:00+00:00", z) == '<time datetime="2026-11-22T04:30:00+08:00">2026-11-22 04:30 UTC+8</time>'
    assert time_html("2026-11-21T20:30:00+00:00", z, with_time=False).endswith(">2026-11-22</time>")
    assert time_html("", z) == "" and time_html("garbage", z) == ""


def test_results_show_dates_in_profile_zone(tmp_path):
    t = make(tmp_path).get("/").text
    assert '<time datetime="2026-11-22T04:30:00+08:00">2026-11-22</time>' in t       # card: date only
    assert "abc.md" in t and "<time datetime=\"2026-11-23T04:00:00+08:00\">2026-11-23</time>" in t   # resume read date, UTC+8


def test_job_page_lists_and_times(tmp_path):
    t = make(tmp_path).get("/job/ashby/1").text
    assert "<li>Python</li>" in t and "<li>Kubernetes</li>" in t and "Python; n8n" not in t
    assert "Location rule:" in t
    assert '<time datetime="2026-11-22T04:30:00+08:00">2026-11-22 04:30 UTC+8</time>' in t
    assert t.index("Draft a cover note") < t.index("Tailor my resume")
    assert "Deal-breakers" in t        # model page keeps the section even when empty


def test_rule_score_page_hides_empty_sections(tmp_path):
    t = make(tmp_path).get("/job/ashby/2").text
    for h in ("Summary", "Matching evidence", "Gaps", "Deal-breakers"):
        assert f">{h}</h2>" not in t
    assert "title matches" in t and "Job description" in t


def test_no_profile_falls_back_to_utc(tmp_path):
    t = make(tmp_path, profile=False).get("/job/ashby/1").text
    assert "2026-11-21 20:30 UTC</time>" in t
