import pytest

from jobfinder import db
from jobfinder.guard import CappedBackend, Refused
from .conftest import FakeBackend, GOOD


def make(tmp_path, cap, kill_file=None):
    events = []

    def log(event, job=None, **f):
        events.append((event, f))

    fake = FakeBackend([GOOD] * 10)
    capped = CappedBackend(fake, str(tmp_path / "t.db"), cap, log, str(kill_file or tmp_path / "KILL"))
    return capped, fake, events


def test_calls_under_the_cap_go_through_and_are_counted(tmp_path):
    capped, fake, events = make(tmp_path, cap=2)
    assert capped.complete("s", "u") == GOOD
    assert capped.complete("s", "u") == GOOD
    assert len(fake.calls) == 2 and db.calls_today(db.connect(str(tmp_path / "t.db"))) == 2
    assert [e for e, _ in events] == ["model_call", "model_call"]


def test_call_over_the_cap_is_refused_and_logged(tmp_path):
    capped, fake, events = make(tmp_path, cap=1)
    capped.complete("s", "u")
    with pytest.raises(Refused):
        capped.complete("s", "u")
    assert len(fake.calls) == 1 and capped.stopped
    assert [f for e, f in events if e == "model_refused"] == [{"reason": "daily_cap", "cap": 1}]
    assert db.calls_today(db.connect(str(tmp_path / "t.db"))) == 1      # a refusal is not a call


def test_kill_switch_setting_blocks_the_next_call(tmp_path):
    capped, fake, events = make(tmp_path, cap=100)
    capped.complete("s", "u")
    con = db.connect(str(tmp_path / "t.db"))
    db.set_setting(con, "kill", "1")
    with pytest.raises(Refused):
        capped.complete("s", "u")
    assert len(fake.calls) == 1
    assert ("model_refused", {"reason": "kill_switch", "cap": 100}) in events
    db.set_setting(con, "kill", "0")
    capped.complete("s", "u")                                           # switched off again: calls work
    assert len(fake.calls) == 2


def test_kill_file_blocks_calls(tmp_path):
    kill = tmp_path / "KILL"
    kill.write_text("", encoding="utf-8")
    capped, fake, _ = make(tmp_path, cap=100, kill_file=kill)
    with pytest.raises(Refused):
        capped.complete("s", "u")
    assert fake.calls == []


def test_inner_backend_is_created_only_on_first_allowed_call(tmp_path):
    made = []
    capped = CappedBackend(lambda: made.append(1) or FakeBackend([GOOD]), str(tmp_path / "t.db"), 0,
                           lambda *a, **k: None, str(tmp_path / "KILL"))
    with pytest.raises(Refused):
        capped.complete("s", "u")
    assert made == []
