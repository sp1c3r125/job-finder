from jobfinder.drafts import draft_note, eligible, write_draft
from jobfinder.scoring import score_job
from .conftest import GOOD, FakeBackend, make_job


def test_score_ok(logs):
    events, log = logs
    assert score_job(FakeBackend([GOOD]), "p", make_job(), log) == GOOD and events == []


def test_retry_once_then_never_repair(logs):
    events, log = logs
    bad = dict(GOOD, fit_score="82")                 # wrong type: not coerced
    b = FakeBackend([bad, bad])
    assert score_job(b, "p", make_job(), log) is None
    assert len(b.calls) == 2 and [e for e, _ in events] == ["score_failed", "score_failed"]


def test_retry_succeeds(logs):
    events, log = logs
    assert score_job(FakeBackend([ValueError("junk"), GOOD]), "p", make_job(), log) == GOOD
    assert len(events) == 1


def test_profile_and_job_in_prompt(logs):
    _, log = logs
    b = FakeBackend([GOOD])
    score_job(b, "name: Alex", make_job(description="Need Rust"), log)
    assert "name: Alex" in b.calls[0][1] and "Need Rust" in b.calls[0][1]


def test_eligibility():
    assert eligible(GOOD, 70)
    assert not eligible(dict(GOOD, fit_score=69), 70)
    assert not eligible(dict(GOOD, deal_breakers=[".NET required"]), 70)


def test_draft_word_limit_and_file(logs, tmp_path):
    _, log = logs
    long = " ".join(["w"] * 200)
    assert draft_note(FakeBackend([{"note": long}, {"note": long}]), "p", make_job(), GOOD, log) is None
    note = draft_note(FakeBackend([{"note": long}, {"note": "Short honest note."}]), "p", make_job(), GOOD, log)
    path = write_draft(make_job(), note, tmp_path)
    assert "Short honest note." in open(path, encoding="utf-8").read()
