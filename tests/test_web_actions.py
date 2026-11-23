import json
import threading
import time

import pytest
from fastapi.testclient import TestClient

from jobfinder import db, web
from .conftest import CFG, GOOD, FakeBackend, make_job


RESUME_TEXT = b"# Alex Example\n" + b"Built agents and automations. " * 20


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)                       # resume.json, resume/, drafts/ all land in tmp
    return tmp_path


def client(tmp_path, **kw):
    path = str(tmp_path / "t.db")
    con = db.connect(path)
    j = make_job()
    db.save(con, j, "2026-11-21T00:00:00+00:00", True, "worldwide")
    db.set_score(con, j, GOOD)
    con.close()
    app = web.create_app(path, dict(CFG, daily_call_cap=5), base_dir=tmp_path, log_path=str(tmp_path / "log.jsonl"),
                         kill_file=str(tmp_path / "KILL"), **kw)
    c = TestClient(app, base_url="http://127.0.0.1", follow_redirects=False)
    token = c.get("/").text.split('name="csrf_token" value="')[1].split('"')[0]
    return c, token, path


def wait_idle(c, text="Last action finished"):
    for _ in range(100):
        t = c.get("/").text
        if text in t or "Failed:" in t:
            return t
        time.sleep(0.05)
    raise AssertionError("background job did not finish")


def test_post_without_token_or_from_other_origin_is_rejected(env):
    c, token, _ = client(env, run_pipeline=lambda: "ok")
    assert c.post("/run", data={"csrf_token": "wrong"}).status_code == 403
    assert c.post("/run", data={}).status_code == 403
    assert c.post("/run", data={"csrf_token": token}, headers={"origin": "https://evil.example"}).status_code == 403
    assert c.post("/run", data={"csrf_token": token}, headers={"origin": "http://127.0.0.1:8000"}).status_code == 303


def test_second_concurrent_run_is_refused(env):
    gate = threading.Event()
    c, token, _ = client(env, run_pipeline=lambda: gate.wait(5) and "done")
    first = c.post("/run", data={"csrf_token": token})
    assert first.headers["location"].endswith("notice=started")
    second = c.post("/run", data={"csrf_token": token})
    assert second.headers["location"].endswith("notice=busy")
    busy = c.get("/").text
    assert "Finding jobs that fit your resume" in busy and "Check progress" in busy and "http-equiv" not in busy
    gate.set()
    wait_idle(c)
    assert c.post("/run", data={"csrf_token": token}).headers["location"].endswith("notice=started")   # free again


def test_failed_run_is_shown_not_hidden(env):
    def boom():
        raise RuntimeError("feed down")

    c, token, _ = client(env, run_pipeline=boom)
    c.post("/run", data={"csrf_token": token})
    assert "feed down" in wait_idle(c)


@pytest.mark.parametrize("name,body,status", [
    ("resume.exe", b"MZ....", 400),
    ("resume.pdf", b"this is not a pdf", 400),
    ("resume.docx", b"not a zip", 400),
    ("resume.txt", b"", 400),
    ("big.txt", b"x" * (web.MAX_UPLOAD + 1), 413),
])
def test_bad_uploads_are_rejected(env, name, body, status):
    c, token, _ = client(env, backend=FakeBackend([]))
    r = c.post("/upload", data={"csrf_token": token}, files={"file": (name, body)})
    assert r.status_code == status
    assert not (env / "resume").exists() or list((env / "resume").iterdir()) == []


def test_good_upload_is_saved_under_a_random_name_and_read(env):
    fake = FakeBackend([Exception("model down")] * 2)
    c, token, _ = client(env, backend=fake)
    r = c.post("/upload", data={"csrf_token": token}, files={"file": ("../../my resume.md", RESUME_TEXT)})
    assert r.status_code == 303
    wait_idle(c)
    [saved] = list((env / "resume").iterdir())
    assert saved.suffix == ".md" and "my" not in saved.name and saved.read_bytes() == RESUME_TEXT
    assert len(fake.calls) == 2                         # one try plus one retry, never repaired
    assert "web_upload" in (env / "log.jsonl").read_text(encoding="utf-8")


def test_kill_switch_from_the_page_blocks_model_calls(env):
    fake = FakeBackend([GOOD] * 5)
    c, token, _ = client(env, backend=fake)
    assert c.post("/kill", data={"csrf_token": token, "on": "1"}).headers["location"].endswith("kill_on")
    assert "Kill switch: <strong>ON</strong>" in c.get("/matches").text
    c.post("/upload", data={"csrf_token": token}, files={"file": ("r.txt", RESUME_TEXT)})
    wait_idle(c)
    assert fake.calls == []                             # nothing reached the model
    log = [json.loads(x) for x in (env / "log.jsonl").read_text(encoding="utf-8").splitlines()]
    assert any(x["event"] == "model_refused" and x["reason"] == "kill_switch" for x in log)
    c.post("/kill", data={"csrf_token": token, "on": "0"})
    assert "Kill switch: <strong>off</strong>" in c.get("/matches").text


def test_tailor_needs_a_resume_and_a_known_job(env):
    c, token, _ = client(env, backend=FakeBackend([]))
    assert c.post("/job/ashby/1/tailor", data={"csrf_token": token}).headers["location"].endswith("no_resume")
    (env / "resume.json").write_text(json.dumps({"name": "Alex"}), encoding="utf-8")
    assert c.post("/job/ashby/zzz/tailor", data={"csrf_token": token}).headers["location"].endswith("no_job")


def test_end_to_end_upload_sample_resume_then_tailor_and_open_docx(env):
    """The M2 manual script, with a mocked model: upload samples/resume.sample.md, import it, tailor one job, open the .docx."""
    from pathlib import Path
    import docx
    sample = (Path(__file__).resolve().parent.parent / "samples" / "resume.sample.md").read_bytes()
    bullet = "Built an invoice-processing workflow in Python and n8n that cut manual entry from 6 hours to 1 hour per week."

    class Model:
        def complete(self, system, user, schema=None, tool_name=None, max_tokens=None):
            if "roles" in (schema or {}).get("properties", {}) and "role_id" not in str(schema):   # resume import
                return {"name": "Alex Example", "headline": "", "contact": ["alex@example.com"], "summary": "Automation developer who builds workflow tools and small LLM apps for small businesses.",
                        "skills": ["Python", "n8n"], "education": [], "certifications": [], "languages": [],
                        "search_keywords": ["automation"], "additional": [],
                        "target": {"titles": ["Automation Developer"], "title_words": ["developer"], "exclude_words": [], "level": "mid"},
                        "roles": [{"title": "Automation Developer", "company": "Example Agency Co.", "dates": "Mar 2023 - Present",
                                   "location": "Cebu", "bullets": [bullet], "groups": []}]}
            return {"summary": "", "skills": ["n8n", "Python"], "gaps": ["No Kubernetes"], "changes": ["moved n8n first"],
                    "roles": [{"role_id": "r0", "bullets": [{"source_id": "r0b0", "text": bullet}], "groups": []}]}

    c, token, path = client(env, backend=Model())
    assert c.post("/upload", data={"csrf_token": token}, files={"file": ("resume.sample.md", sample)}).status_code == 303
    wait_idle(c)
    assert json.loads((env / "resume.json").read_text(encoding="utf-8"))["name"] == "Alex Example"
    assert "Tailor my resume" in c.get("/job/ashby/1").text
    assert c.post("/job/ashby/1/tailor", data={"csrf_token": token}).status_code == 303
    page = wait_idle(c, "tailored resume written")
    assert "tailored resume written" in page
    [path_docx] = list((env / "resumes").glob("*.docx"))
    assert "n8n" in "\n".join(p.text for p in docx.Document(str(path_docx)).paragraphs)
    assert c.get(f"/files/resumes/{path_docx.name}").status_code == 200


def test_cover_note_needs_a_profile(env):
    c, token, _ = client(env, backend=FakeBackend([]))
    assert c.post("/job/ashby/1/note", data={"csrf_token": token}).headers["location"].endswith("no_profile")


def test_cover_note_is_drafted_only_on_request_and_saved(env):
    (env / "profile.yaml").write_text("summary: Automation developer.\n", encoding="utf-8")
    b = FakeBackend([{"note": "I build n8n workflows. Gap: no Kubernetes."}])
    c, token, path = client(env, backend=b)
    page = c.get("/job/ashby/1").text
    assert "Draft a cover note" in page and b.calls == []                                # nothing is drafted by itself
    assert c.post("/job/ashby/1/note", data={}).status_code == 403                     # CSRF token needed
    assert c.post("/job/ashby/zzz/note", data={"csrf_token": token}).headers["location"].endswith("no_job")
    assert c.post("/job/ashby/1/note", data={"csrf_token": token}).status_code == 303
    wait_idle(c, "cover note drafted")
    assert len(b.calls) == 1
    [md] = list((env / "drafts").glob("*.md"))
    assert "no Kubernetes" in md.read_text(encoding="utf-8")
    assert "Cover note draft" in c.get("/job/ashby/1").text


def test_cover_note_for_a_job_with_only_a_rule_score(env):
    (env / "profile.yaml").write_text("summary: Automation developer.\n", encoding="utf-8")
    b = FakeBackend([{"note": "I build n8n workflows."}])
    c, token, path = client(env, backend=b)
    con = db.connect(path)
    j = make_job(source_id="r1", title="AI Automation Engineer")
    db.save(con, j, "2026-11-21T00:00:00+00:00", True, "worldwide")
    db.set_rule_scores(con, [(j, {"score": 61, "reasons": ["title 40/40: exactly your target title"], "deal_breakers": []})])
    con.close()
    page = c.get("/job/ashby/r1").text
    assert "Rule score" in page and "title 40/40" in page and "has not read this job" in page
    assert c.post("/job/ashby/r1/note", data={"csrf_token": token}).status_code == 303
    wait_idle(c, "cover note drafted")
    assert len(b.calls) == 1


def test_failed_task_writes_one_log_line(tmp_path):
    import json as _json
    from jobfinder.runner import Runner
    lines = []
    r = Runner(log=lambda event, job=None, **f: lines.append((event, f)))

    def boom():
        raise ValueError("x" * 500)
    assert r.start("Search run", boom)
    for _ in range(100):
        if r.status()["state"] == "failed":
            break
        time.sleep(0.02)
    assert len(lines) == 1 and lines[0][0] == "task_failed"
    f = lines[0][1]
    assert f["label"] == "Search run" and f["error_type"] == "ValueError" and len(f["error"]) <= 300
