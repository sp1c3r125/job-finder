import json
import re

import pytest
import yaml
from fastapi.testclient import TestClient

from jobfinder import db, demo, web
from .conftest import CFG

ROOT = web.ROOT


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "resume.json").write_text(json.dumps({"name": "Secret Person", "text": "x"}), encoding="utf-8")
    (tmp_path / "drafts").mkdir()
    (tmp_path / "drafts" / "real.md").write_text("real private note", encoding="utf-8")
    app = web.create_app(str(tmp_path / "demo.db"), dict(CFG), base_dir=tmp_path, log_path=str(tmp_path / "log.jsonl"), demo=True)
    return TestClient(app, base_url="http://127.0.0.1", follow_redirects=False)


def test_demo_shows_only_invented_jobs(client):
    page = client.get("/").text
    assert page.count("Fake Company") >= 20 and "Demo (invented)" in page
    assert "Secret Person" not in page and "Personal use only" not in page
    assert "Invented" in page and "invented" in page
    names = [c["board"].lower() for c in yaml.safe_load((ROOT / "companies.yaml").read_text(encoding="utf-8"))["companies"]]
    text = re.sub(r"<[^>]+>", " ", page).lower()
    assert not [n for n in names if re.search(rf"\b{re.escape(n)}\b", text)]      # no real company from companies.yaml


def test_demo_has_no_actions_or_downloads(client):
    page = client.get("/").text
    assert "Run search now" not in page and 'action="/upload"' not in page and "csrf_token" not in page
    assert "Tailor my resume" not in client.get("/job/demo/1").text
    assert client.get("/files/drafts/real.md").status_code == 404


@pytest.mark.parametrize("method", ["post", "put", "patch", "delete"])
@pytest.mark.parametrize("path", ["/run", "/upload", "/kill", "/job/demo/1/tailor", "/job/demo/1/note"])
def test_every_write_route_is_405(client, method, path):
    assert getattr(client, method)(path).status_code == 405


def test_about_page_says_the_data_is_invented(client):
    t = client.get("/about").text
    assert "invented" in t and "example.invalid" in t


def test_seed_is_idempotent_and_makes_no_model_call(tmp_path):
    path = str(tmp_path / "d.db")
    assert demo.seed(path) == 20 and demo.seed(path) == 0
    rows = db.scored_jobs(db.connect(path))
    assert len(rows) == 20 and all(r["company"].startswith("Fake Company") and r["source"] == "demo" for r in rows)
    scores = [json.loads(r["score_json"])["fit_score"] for r in rows]
    assert max(scores) >= 80 and min(scores) < 60


def test_real_dashboard_is_unaffected_by_demo_flag(tmp_path):
    app = web.create_app(str(tmp_path / "t.db"), dict(CFG), base_dir=tmp_path, log_path=str(tmp_path / "l.jsonl"))
    c = TestClient(app, base_url="http://127.0.0.1", follow_redirects=False)
    assert "Personal use only" in c.get("/").text and c.post("/run").status_code == 403
