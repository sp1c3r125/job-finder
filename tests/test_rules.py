"""Rule score (plain code, no model): parts, deal-breakers, storage, listing, gating of model calls, calibration."""
import shutil
from datetime import datetime, timezone
from pathlib import Path

from jobfinder import calibrate, db, main, report, rules
from .conftest import CFG, GOOD, TARGET, FakeBackend, make_job, recent

ROOT = Path(__file__).resolve().parent.parent
RES = {"skills": ["n8n", "Python", "Make", "Claude API", "SQL"]}
FACTS = {"years_total": 9.0, "years_ai": 3.0, "known_gaps": ["Kotlin", "Swift"]}
CFG_T = dict(CFG, target=TARGET)


def score(job, cfg=CFG_T, res=RES, facts=FACTS):
    return rules.rule_score(job, cfg, res, facts)


def test_title_exact_beats_partial_beats_title_word_only():
    exact = score(make_job(title="AI Automation Engineer"))["parts"]["title"]
    partial = score(make_job(title="AI Automation Specialist"))["parts"]["title"]
    word = score(make_job(title="Support Engineer"))["parts"]["title"]
    assert exact == 40 and exact > partial > word > 0
    assert rules.title_strength("Payments AI Automation Engineer", TARGET)[0] == 0.9     # target title inside the job title


def test_level_in_the_title_must_fit_the_profile_level():
    mid = score(make_job(title="AI Automation Engineer"))["parts"]["title"]
    senior = score(make_job(title="Senior AI Automation Engineer"))["parts"]["title"]
    lead = score(make_job(title="Staff AI Automation Engineer"))["parts"]["title"]
    assert mid > senior > lead


def test_skills_are_whole_words_and_make_needs_a_capital():
    def skills(desc):
        return score(make_job(description=desc))["parts"]["skills"]
    assert skills("We make things. Pythonic code.") == 0                  # 'make' the verb, 'Pythonic' is not Python
    assert skills("We use n8n and Python.") > 0
    assert skills("We use Make.") > 0 and skills("We make it.") == 0
    assert score(make_job(description="n8n, Python, SQL, Claude API, Make"))["parts"]["skills"] == 30    # capped at the full share


def test_years_asked_above_yours_lowers_the_score():
    def y(desc):
        return score(make_job(description=desc))["parts"]["years"]
    assert y("2+ years of experience building agents") == 15
    assert y("5+ years of experience building agents") < 15
    assert y("8+ years of experience building agents") < y("5+ years of experience building agents")
    assert y("Join us. 20 years in business.") == 12                         # not about experience: no years asked
    assert rules.years_asked("3-5 years of experience in AI") == (3, True)
    assert rules.years_asked("Minimum 2 yrs. of experience") == (2, False)


def test_years_in_the_ai_field_are_compared_with_your_ai_years():
    ai = score(make_job(description="5+ years of AI experience required."))
    assert "AI/automation" in ai["reasons"][2] and ai["parts"]["years"] < 15            # 5 asked, 2 AI years
    other = score(make_job(description="5+ years of experience in customer support required."))
    assert "total" in other["reasons"][2] and other["parts"]["years"] == 15             # 5 asked, 7 total years
    far = score(make_job(description="Needs 5 years of experience with LLM agents."))
    assert far["parts"]["years"] == 15 * 0.5                                           # 4 asked vs 2 AI years


def test_years_you_have_are_numbers_in_profile_yaml(tmp_path):
    p = tmp_path / "profile.yaml"
    sentence = "years_experience: " + repr("AI: about 3 years. Total: about 9 years.")
    p.write_text(sentence + "\nyears_ai_automation: 3\nyears_total: 9\nknown_gaps: [Kotlin]\n", encoding="utf-8")
    assert rules.load_facts(p) == {"years_total": 9.0, "years_ai": 3.0, "known_gaps": ["Kotlin"]}
    p.write_text("years_experience: 4\n", encoding="utf-8")                              # older profile: one number
    assert rules.load_facts(p) == {"years_total": 4.0, "years_ai": 4.0, "known_gaps": []}
    p.write_text("years_experience: 'about 9 years'\n", encoding="utf-8")                # a sentence is not parsed
    assert rules.load_facts(p)["years_total"] is None
    assert rules.load_facts(tmp_path / "none.yaml") == {"years_total": None, "years_ai": None, "known_gaps": []}


def test_deal_breakers_are_found_and_cap_the_score():
    clean = make_job(description="n8n Python SQL Claude API. Remote.")
    assert score(clean)["deal_breakers"] == [] and score(clean)["score"] > 50
    for text, label in [("Candidates must be US only.", "US only"),
                        ("You must be authorized to work in the United States.", "US only"),
                        ("Must hold US citizenship.", "citizenship required"),
                        ("An active security clearance is needed.", "security clearance"),
                        ("This is an on-site role in Austin.", "on-site"),
                        ("Strong Kotlin experience is required.", "Kotlin required")]:
        r = score(make_job(description="n8n Python SQL Claude API. " + text))
        assert label in r["deal_breakers"], text
        assert r["score"] <= rules.DEAL_BREAKER_CAP
        assert any("deal-breakers" in x for x in r["reasons"])


def test_deal_breaker_words_with_a_negation_or_a_soft_cue_do_not_count():
    for text in ["No security clearance is needed.", "Fully remote, not on-site.", "On-site or remote, your choice.",
                 "Hiring regardless of citizenship.", "Kotlin is a nice to have.", "Swift is a bonus."]:
        assert score(make_job(description=text))["deal_breakers"] == [], text
    assert "Kotlin required" in score(make_job(title="Backend Developer (Kotlin)", description="Build services."))["deal_breakers"]


def test_location_part_prefers_a_stated_location_and_a_remote_flag():
    full = score(make_job(location="Philippines", remote=True))["parts"]["location"]
    ww = score(make_job(location="Worldwide", remote=True))["parts"]["location"]
    inferred = score(make_job(location="Worldwide", remote=None))["parts"]["location"]
    assert full == 15 and full > ww > inferred


def test_no_resume_and_no_target_still_gives_a_usable_score():
    cfg = dict(CFG)                                   # no target: the keywords stand in
    r = rules.rule_score(make_job(), cfg, None, {"years_total": None, "years_ai": None, "known_gaps": []})
    assert r["score"] >= 40 and "keyword" in r["reasons"][0]
    assert rules.rule_score(make_job(title="Cook", description="Kitchen."), cfg, None, {})["score"] < 40


def test_reasons_are_kept_with_the_score(tmp_path):
    con = db.connect(str(tmp_path / "t.db"))
    j = make_job()
    db.save(con, j, recent(), True, "worldwide")
    r = score(j)
    db.set_rule_scores(con, [(j, r)])
    [row] = db.scored_jobs(con)                       # listed with no model score
    out = report.row_from(row)
    assert out["score_kind"] == "rule" and out["score"] == r["score"] and out["rule_reasons"] == r["reasons"]
    assert "Score type" in report.COLUMNS
    db.set_score(con, j, GOOD)
    assert report.row_from(db.scored_jobs(con)[0])["score_kind"] == "model"


def test_rule_only_jobs_list_after_model_scored_ones_and_age_out(tmp_path):
    con = db.connect(str(tmp_path / "t.db"))
    a, b = make_job(source_id="a", title="A"), make_job(source_id="b", title="B")
    old = make_job(source_id="o", title="O", posted="2020-01-01T00:00:00+00:00")
    for j, sc in ((a, 90), (b, 60), (old, 99)):
        db.save(con, j, recent(), True, "worldwide")
        db.set_rule_scores(con, [(j, {"score": sc, "reasons": [], "deal_breakers": []})])
    db.set_score(con, b, dict(GOOD, fit_score=50))
    rs = report.rows(con)
    assert [r["source_id"] for r in rs] == ["b", "a"]            # model-scored first, then rule-only; the old one is not listed
    assert [r["score_kind"] for r in rs] == ["model", "rule"]


# --- gating of model calls in the daily run --------------------------------------------------------------------

def _run(tmp_path, monkeypatch, jobs, scored_today=0, **cfg_extra):
    import yaml
    monkeypatch.chdir(tmp_path)
    cfg = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
    cfg.update(research=False, aggregators=[], tailor_resume=False, **cfg_extra)
    Path("config.yaml").write_text(yaml.safe_dump(cfg), encoding="utf-8")
    shutil.copy(ROOT / "profile.example.yaml", "profile.yaml")
    Path("companies.yaml").write_text("companies: []\n")
    con = db.connect("t.db")
    now = datetime.now(timezone.utc).isoformat()
    for j in jobs:
        db.save(con, j, now, True, "worldwide")
    for i in range(scored_today):
        other = make_job(source_id=f"s{i}", title="Other")
        db.save(con, other, now, True, "worldwide")
        db.set_score(con, other, GOOD)
    con.close()
    backend = FakeBackend([GOOD] * 20)
    assert main.run(["--db", "t.db"], backend=backend, http=object()) == 0
    con = db.connect("t.db")
    done = {r[0] for r in con.execute("SELECT source_id FROM job_scores WHERE score_json IS NOT NULL AND source_id NOT LIKE 's%'")}
    return backend, done


def test_only_the_best_rule_scores_go_to_the_model(tmp_path, monkeypatch, capsys):
    jobs = [make_job(source_id="best", title="AI Automation Engineer", description="n8n Python agents. Philippines."),
            make_job(source_id="mid", title="AI Support Specialist", description="Help customers."),
            make_job(source_id="low", title="Quality Checker", description="Test things.")]
    backend, done = _run(tmp_path, monkeypatch, jobs, model_daily_jobs=1, rule_min=0)
    assert done == {"best"} and model_scoring_calls(backend) == 1                     # the daily limit keeps only the best
    assert "3 jobs waiting" in capsys.readouterr().out


def test_rule_min_keeps_weak_jobs_away_from_the_model(tmp_path, monkeypatch):
    jobs = [make_job(source_id="best", description="n8n Python agents."),
            make_job(source_id="us", description="US only. On-site.")]
    backend, done = _run(tmp_path, monkeypatch, jobs, model_daily_jobs=15, rule_min=40)
    assert done == {"best"} and model_scoring_calls(backend) == 1


def test_jobs_already_scored_today_use_up_the_daily_limit(tmp_path, monkeypatch):
    jobs = [make_job(source_id="a", description="n8n Python")]
    backend, done = _run(tmp_path, monkeypatch, jobs, scored_today=2, model_daily_jobs=2, rule_min=0)
    assert done == set() and backend.calls == []


def test_waiting_jobs_show_in_the_report_as_rule_score(tmp_path, monkeypatch):
    jobs = [make_job(source_id="a", description="n8n Python")]
    _run(tmp_path, monkeypatch, jobs, model_daily_jobs=0, rule_min=0)
    assert "rule score" in Path("dashboard.html").read_text(encoding="utf-8")


# --- calibration -----------------------------------------------------------------------------------------------

def test_calibration_numbers():
    assert round(calibrate.pearson([1, 2, 3, 4], [2, 4, 6, 8]), 3) == 1.0
    assert calibrate.pearson([1, 1, 1], [1, 2, 3]) == 0.0
    assert calibrate.ranks([5, 5, 1]) == [2.5, 2.5, 1.0]
    strong = make_job(source_id="s", description="n8n Python SQL Claude API")
    weak = make_job(source_id="w", title="Support Engineer", description="Help.")
    rows, s = calibrate.compare([(weak, 20), (strong, 85)], CFG_T, RES, FACTS, top=1)
    assert [x["job"].source_id for x in rows] == ["s", "w"]
    assert s["strong_in_top"] and s["strong"][0][1] == 85 and s["pearson"] > 0


def model_scoring_calls(backend):
    return sum(1 for c in backend.calls if c[0].startswith("You score"))
