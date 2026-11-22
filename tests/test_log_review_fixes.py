"""One test per bug found in the log review (A1 to A4)."""
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from jobfinder import db, main, resume, web
from jobfinder.sources import ashby
from .conftest import CFG, GOOD, FakeBackend, FakeHttp, make_job, recent
from .test_web_actions import client, wait_idle

ROOT = Path(__file__).resolve().parent.parent


def _setup(tmp_path, monkeypatch, profile=True):
    monkeypatch.chdir(tmp_path)
    shutil.copy(ROOT / "config.yaml", ".")
    if profile:
        shutil.copy(ROOT / "profile.example.yaml", "profile.yaml")
    Path("companies.yaml").write_text("companies:\n  - {name: Acme, source: ashby, board: acme}\n")
    job = {"id": "1", "title": "AI Automation Engineer", "jobUrl": "https://a/1", "publishedAt": recent(),
           "location": "Remote - Worldwide", "workplaceType": "Remote", "descriptionPlain": "LLM agents"}
    return FakeHttp({ashby.URL.format(board="acme"): {"jobs": [job]}})


def test_a1_run_without_profile_stops_before_saving_anything(tmp_path, monkeypatch):
    http = _setup(tmp_path, monkeypatch, profile=False)
    with pytest.raises(SystemExit):
        main.run(["--db", "t.db"], backend=FakeBackend([]), http=http)
    assert http.calls == []                                   # nothing fetched
    con = db.connect("t.db")
    assert con.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 0   # test file only; SQL allowed in tests


def test_a1_saved_but_unscored_jobs_are_scored_on_the_next_run(tmp_path, monkeypatch):
    http = _setup(tmp_path, monkeypatch)
    con = db.connect("t.db")
    now = datetime.now(timezone.utc)
    ok, stale, expired, rejected = (make_job(source_id=i, url=f"https://x/{i}") for i in "abcd")
    stale.posted = "2020-01-01T00:00:00+00:00"
    expired.expires = "2020-01-01T00:00:00+00:00"
    for j, passed in ((ok, True), (stale, True), (expired, True), (rejected, False)):
        db.save(con, j, now.isoformat(), passed, "worldwide" if passed else "no keyword match")
    con.close()
    backend = FakeBackend([GOOD, {"note": "I build n8n workflows. Gap: no Kubernetes."}, GOOD, {"note": "x"}])
    assert main.run(["--db", "t.db"], backend=backend, http=http) == 0
    con = db.connect("t.db")
    scored = {r[0] for r in con.execute("SELECT source_id FROM job_scores WHERE score_json IS NOT NULL")}
    assert "a" in scored                                      # old saved job now scored
    assert not scored & {"b", "c", "d"}                       # stale, expired and rejected jobs are not


def test_a2_refresh_link_goes_to_root_and_result_is_shown_once(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert 'href="">Refresh' not in (ROOT / "jobfinder/templates/base.html").read_text(encoding="utf-8")
    c, token, _ = client(tmp_path, run_pipeline=lambda: "run finished")
    c.post("/run", data={"csrf_token": token})
    assert "run finished" in wait_idle(c)
    again = c.get("/?notice=started").text
    assert "run finished" not in again and "Started. Select Refresh" not in again   # shown once, then gone


def test_a3_readme_in_resume_folder_is_not_a_resume(tmp_path):
    (tmp_path / "README.md").write_text("how to use this folder")
    assert resume.newest_drop(tmp_path) is None
    (tmp_path / "cv.md").write_text("# Me")
    assert resume.newest_drop(tmp_path).name == "cv.md"


def test_a4_web_uses_the_project_folder_for_resume_json(tmp_path, monkeypatch):
    project, elsewhere = tmp_path / "project", tmp_path / "elsewhere"
    project.mkdir(); elsewhere.mkdir()
    (project / "resume.json").write_text(json.dumps({"name": "Alex Project", "roles": [], "skills": []}), encoding="utf-8")
    monkeypatch.chdir(elsewhere)                              # current folder has no resume.json
    app = web.create_app(str(project / "t.db"), dict(CFG), base_dir=project, log_path=str(project / "l.jsonl"),
                         kill_file=str(project / "KILL"))
    page = TestClient(app, base_url="http://127.0.0.1").get("/").text
    assert "Alex Project" in page
    assert not (elsewhere / "resume.json").exists()


def test_a4_run_reads_profile_from_root_not_current_folder(tmp_path, monkeypatch):
    http = _setup(tmp_path, monkeypatch)
    root = tmp_path / "root"
    root.mkdir()
    shutil.copy(ROOT / "profile.example.yaml", root / "profile.yaml")
    Path("profile.yaml").unlink()                             # cwd has none; root has one
    backend = FakeBackend([GOOD, {"note": "n"}])
    assert main.run(["--db", "t.db"], backend=backend, http=http, root=root) == 0
    assert backend.calls
