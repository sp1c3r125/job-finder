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


def test_matches_lists_jobs_best_first_and_escapes(tmp_path):
    r = make_client(tmp_path).get("/matches")
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


def test_security_headers_on_every_response_and_no_store_on_html(tmp_path):
    from fastapi.testclient import TestClient
    app = web.create_app(str(tmp_path / "h.db"), dict(CFG), base_dir=tmp_path, log_path=str(tmp_path / "l.jsonl"))
    c = TestClient(app, base_url="http://127.0.0.1")
    for path in ("/", "/matches", "/about", "/static/style.css", "/nope"):
        h = c.get(path).headers
        assert h["x-content-type-options"] == "nosniff" and h["referrer-policy"] == "strict-origin-when-cross-origin"
        assert h["content-security-policy"] == web.SECURITY_HEADERS["Content-Security-Policy"]
        for part in ("default-src 'self'", "frame-ancestors 'none'", "form-action 'self'", "base-uri 'none'", "object-src 'none'"):
            assert part in h["content-security-policy"]
    assert c.get("/").headers["cache-control"] == "no-store"
    assert "no-store" not in c.get("/static/style.css").headers.get("cache-control", "")


def test_resume_panel_and_no_meta_refresh(tmp_path, monkeypatch):
    import json
    from fastapi.testclient import TestClient
    monkeypatch.chdir(tmp_path)
    app = web.create_app(str(tmp_path / "h.db"), dict(CFG), base_dir=tmp_path, log_path=str(tmp_path / "l.jsonl"))
    c = TestClient(app, base_url="http://127.0.0.1")
    assert "Choose your resume" in c.get("/").text and "No resume yet" in c.get("/matches").text
    (tmp_path / "resume.json").write_text(json.dumps({"name": "Alex Example", "source_file": "abc.md", "imported_at": "2026-11-22T01:02:03+00:00",
        "roles": [{}, {}], "skills": ["Python"], "search_keywords": ["automation", "n8n"], "left_out": ["skill: Kubernetes"]}), encoding="utf-8")
    t = c.get("/").text
    for x in ("abc.md", "2026-11-22", "Alex Example", "automation, n8n", "skill: Kubernetes"):
        assert x in t
    assert "http-equiv" not in t
