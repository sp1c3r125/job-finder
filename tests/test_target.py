import json
from pathlib import Path

import pytest
import yaml

from jobfinder import resume, target, web
from jobfinder.prefilter import prefilter
from jobfinder.sources import himalayas
from .conftest import CFG, TARGET, FakeBackend, FakeHttp, make_job, recent

TCFG = dict(CFG, target=TARGET)


# --- title check (plain code) ---------------------------------------------------------------------------------

def test_title_word_passes_and_other_rules_still_apply():
    assert prefilter(make_job(title="Senior AI Engineer"), TCFG) == (True, "worldwide")
    assert prefilter(make_job(title="AI Engineer", posted=recent(10)), TCFG)[1] == "older than max_age_days"
    assert prefilter(make_job(title="AI Engineer", remote=False), TCFG)[1] == "not remote"
    assert prefilter(make_job(title="AI Engineer", location="Remote - US only"), TCFG)[1] == "location not allowed"


def test_description_keyword_alone_is_not_enough():
    job = make_job(title="Frontend Developer", description="Build LLM agents and automation with AI.")
    assert prefilter(job, CFG)[0]                       # old rule (no search profile): the description keyword passes
    ok, why = prefilter(job, TCFG)
    assert not ok and why == "title matches no target title word"


def test_exclude_word_in_title_rejects_even_with_a_title_word():
    for title in ("AI Trainer", "Talent Pool - AI Engineer", "Engineering Intern", "Data Annotator (AI)"):
        ok, why = prefilter(make_job(title=title), TCFG)
        assert not ok and why.startswith("title has exclude word"), title


def test_exclude_word_only_in_description_does_not_reject():
    assert prefilter(make_job(title="AI Engineer", description="You will not be an intern or a trainer."), TCFG)[0]


def test_title_words_match_whole_words_only():
    assert not prefilter(make_job(title="Reengineering Lead"), TCFG)[0]
    assert prefilter(make_job(title="Forward Deployed Engineer"), dict(CFG, target=dict(TARGET, title_words=["forward deployed"])))[0]
    assert prefilter(make_job(title="AI Engineer"), dict(CFG, target=dict(TARGET, title_words=["AI"])))[0]


# --- Himalayas uses the target titles -------------------------------------------------------------------------

def test_himalayas_queries_target_titles_not_keywords():
    cfg = dict(TCFG, keywords=["AI", "agent"], himalayas={"max_pages": 1})

    class H(FakeHttp):
        def get_json(self, url, params=None):
            self.calls.append((url, params))
            return {"jobs": []}

    http = H({})
    himalayas.fetch(http, cfg)
    assert [c[1]["q"] for c in http.calls] == TARGET["titles"]
    http = H({})
    himalayas.fetch(http, dict(cfg, target=None))
    assert [c[1]["q"] for c in http.calls] == ["AI", "agent"]


# --- the profile: made, saved, overridden ---------------------------------------------------------------------

def test_clean_trims_dedupes_and_needs_titles():
    t = target.clean({"titles": [" A  B ", "a b", ""], "title_words": ["x"], "exclude_words": ["Y", "y"], "level": " mid "})
    assert t == {"titles": ["A B"], "title_words": ["x"], "exclude_words": ["Y"], "level": "mid"}
    with pytest.raises(ValueError):
        target.clean({"titles": [], "title_words": ["x"], "exclude_words": [], "level": ""})


def test_make_target_one_call_and_retry_then_none(logs):
    events, log = logs
    b = FakeBackend([TARGET])
    assert target.make_target(b, {"summary": "s", "skills": ["Python"]}, log) == TARGET
    assert len(b.calls) == 1 and "Python" in b.calls[0][1]
    b = FakeBackend([{"titles": []}, RuntimeError("down")])          # wrong shape, then an error: never repaired
    assert target.make_target(b, {"summary": "s"}, log) is None
    assert [e for e, _ in events].count("target_failed") == 2


def test_save_target_keeps_the_other_keys(tmp_path):
    p = tmp_path / "resume.json"
    p.write_text(json.dumps({"name": "Alex", "skills": ["Python"]}), encoding="utf-8")
    target.save_target(TARGET, p)
    assert json.loads(p.read_text(encoding="utf-8")) == {"name": "Alex", "skills": ["Python"], "target": TARGET}


def test_target_yaml_overrides_resume_json_and_a_broken_one_falls_back(tmp_path):
    res = {"target": TARGET}
    assert target.load_target(res, tmp_path) == (TARGET, "resume.json")
    (tmp_path / "target.yaml").write_text(yaml.safe_dump(dict(TARGET, exclude_words=["frontend"])), encoding="utf-8")
    t, source = target.load_target(res, tmp_path)
    assert (source, t["exclude_words"]) == ("target.yaml", ["frontend"])
    (tmp_path / "target.yaml").write_text("titles: [", encoding="utf-8")
    assert target.load_target(res, tmp_path) == (TARGET, "resume.json")
    assert target.load_target(None, tmp_path / "nowhere") == (None, "")
    assert target.with_target({"a": 1}, None, tmp_path / "nowhere") == {"a": 1}


def test_import_saves_the_profile_in_resume_json(tmp_path):
    from .test_resume import EXTRACTED as RAW, TEXT
    src = tmp_path / "cv.md"
    src.write_text(TEXT, encoding="utf-8")
    out = tmp_path / "resume.json"
    got = resume.import_resume(FakeBackend([RAW]), src, lambda *a, **k: None, out)
    assert got and json.loads(out.read_text(encoding="utf-8"))["target"] == RAW["target"]


def test_dashboard_shows_the_search_profile_and_the_override(tmp_path):
    from fastapi.testclient import TestClient
    (tmp_path / "resume.json").write_text(json.dumps({"name": "Alex", "target": TARGET}), encoding="utf-8")
    c = TestClient(web.create_app(db_path=str(tmp_path / "j.db"), cfg=CFG, base_dir=tmp_path, log_path=str(tmp_path / "l.jsonl")),
                   headers={"host": "127.0.0.1"})
    html = c.get("/matches").text
    assert "Roles we search for" in html and "AI Automation Engineer" in html and "trainer" in html and "Made from your resume" in html
    (tmp_path / "target.yaml").write_text(yaml.safe_dump(dict(TARGET, titles=["Edited Title"])), encoding="utf-8")
    html = c.get("/matches").text
    assert "Edited Title" in html and "your edits" in html


def test_exclude_and_title_words_allow_a_plural():
    assert not prefilter(make_job(title="Freelance AI Trainers"), TCFG)[0]
    assert prefilter(make_job(title="Automation Engineers"), TCFG)[0]
