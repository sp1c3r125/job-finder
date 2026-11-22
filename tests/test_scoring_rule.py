import yaml

from jobfinder import scoring
from .conftest import GOOD, FakeBackend, make_job


def test_work_hours_are_a_deal_breaker_only_when_they_conflict_with_the_profile_constraints():
    s = scoring.SYSTEM
    fixed = s.split("deal_breakers:")[1].split("Work-hour")[0]
    assert "night-shift" not in fixed and "US-hours" not in fixed               # no longer in the fixed list
    assert "ONLY if they conflict with the constraints in the PROFILE" in " ".join(s.split())
    assert "US-only or other region restrictions that exclude the Philippines" in " ".join(fixed.split())      # region rules stay


def test_the_profile_constraint_reaches_the_model(tmp_path):
    p = tmp_path / "profile.yaml"
    p.write_text(yaml.safe_dump({"name": "Alex", "constraints": "none. Available for evening and overnight shifts."}),
                 encoding="utf-8")
    profile = scoring.load_profile(str(p), str(tmp_path / "no-resume.json"))
    b = FakeBackend([GOOD])
    scoring.score_job(b, profile, make_job(description="Night shift, US hours."), lambda *a, **k: None)
    system, user = b.calls[0]
    assert "overnight shifts" in user and "Work-hour requirements" in system
