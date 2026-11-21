import json
import shutil
from pathlib import Path

from jobfinder import main
from jobfinder.sources import ashby
from .conftest import GOOD, FakeBackend, FakeHttp, recent

ROOT = Path(__file__).resolve().parent.parent


def test_full_run_with_mocks(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    shutil.copy(ROOT / "config.yaml", ".")
    shutil.copy(ROOT / "profile.example.yaml", "profile.yaml")
    Path("companies.yaml").write_text("companies:\n  - {name: Acme, source: ashby, board: acme}\n")
    job = {"id": "1", "title": "AI Automation Engineer", "jobUrl": "https://a/1", "publishedAt": recent(),
           "location": "Remote - Worldwide", "workplaceType": "Remote", "descriptionPlain": "LLM agents"}
    http = FakeHttp({ashby.URL.format(board="acme"): {"jobs": [job]}})
    backend = FakeBackend([GOOD, {"note": "I build n8n workflows. Gap: no Kubernetes."}])
    assert main.run(["--db", "t.db"], backend=backend, http=http) == 0
    assert Path("report.xlsx").exists() and "AI Automation Engineer" in Path("dashboard.html").read_text()
    assert len(list(Path("drafts").glob("*.md"))) == 1
    # second run: the job is already seen, so the model is not called again
    backend2 = FakeBackend([])
    main.run(["--db", "t.db"], backend=backend2, http=http)
    assert backend2.calls == []
    events = [json.loads(l)["event"] for l in Path("logs/decisions.jsonl").read_text().splitlines()]
    assert "prefilter_pass" in events and "scored" in events
