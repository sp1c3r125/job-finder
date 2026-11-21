from fastapi.testclient import TestClient

from jobfinder import db, web
from .conftest import CFG, GOOD, make_job

NOW = "2026-11-21T00:00:00+00:00"


def make_client(tmp_path, **cfg):
    path = str(tmp_path / "t.db")
    con = db.connect(path)
    for sid, title, score, src in (("1", "AI Engineer", 82, "ashby"), ("2", "Automation Lead", 91, "remoteok"),
                                   ("3", "Agent <b>Dev</b>", 75, "wwr")):
        j = make_job(source=src, source_id=sid, title=title, company="Acme")
        db.save(con, j, NOW, True, "worldwide")
        db.set_score(con, j, dict(GOOD, fit_score=score), "drafts/d.md" if sid == "1" else None)
    con.close()
    (tmp_path / "drafts").mkdir()
    (tmp_path / "drafts" / "d.md").write_text("hello note", encoding="utf-8")
    (tmp_path / "secret.txt").write_text("no", encoding="utf-8")
    app = web.create_app(path, dict(CFG, **cfg), base_dir=tmp_path)
    return TestClient(app, base_url="http://127.0.0.1")


def test_dashboard_lists_jobs_best_first_and_escapes(tmp_path):
    r = make_client(tmp_path).get("/")
    assert r.status_code == 200
    t = r.text
    assert t.index("Automation Lead") < t.index("AI Engineer") < t.index("Agent &lt;b&gt;Dev")
    assert "Agent <b>Dev" not in t
    assert "via <a href=" in t and "Remote OK" in t and "We Work Remotely" in t
    assert 'lang="en"' in t and "color-scheme" in t


def test_wrong_host_is_rejected(tmp_path):
    c = make_client(tmp_path)
    assert c.get("/", headers={"host": "evil.example.com"}).status_code == 400
    assert c.get("/", headers={"host": "localhost:8000"}).status_code == 200


def test_tunnel_host_can_be_allowed_in_config(tmp_path):
    c = make_client(tmp_path, allowed_hosts=["127.0.0.1", "me.tunnel.example"])
    assert c.get("/", headers={"host": "me.tunnel.example"}).status_code == 200


def test_job_page_and_missing_job(tmp_path):
    c = make_client(tmp_path)
    assert "Matching evidence" in c.get("/job/ashby/1").text
    assert c.get("/job/ashby/nope").status_code == 404


def test_files_only_from_drafts_and_resumes(tmp_path):
    c = make_client(tmp_path)
    assert c.get("/files/drafts/d.md").text == "hello note"
    assert c.get("/files/secret.txt").status_code == 404
    assert c.get("/files/drafts/../secret.txt").status_code == 404
    assert c.get("/files/..%2Fsecret.txt").status_code == 404


def test_no_write_routes(tmp_path):
    assert make_client(tmp_path).post("/").status_code == 405
