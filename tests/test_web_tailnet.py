"""ALLOWED_HOSTS_EXTRA: the tailnet hostname (invented here) is accepted for Host and Origin; everything else stays refused."""
import pytest
from fastapi.testclient import TestClient

from jobfinder import web
from .conftest import CFG

HOST = "box.example-tailnet.ts.net"


def make(tmp_path, monkeypatch, extra, host=HOST):
    monkeypatch.chdir(tmp_path)
    if extra is None:
        monkeypatch.delenv("ALLOWED_HOSTS_EXTRA", raising=False)
    else:
        monkeypatch.setenv("ALLOWED_HOSTS_EXTRA", extra)
    app = web.create_app(str(tmp_path / "t.db"), dict(CFG), base_dir=tmp_path, log_path=str(tmp_path / "log.jsonl"),
                         kill_file=str(tmp_path / "KILL"), run_pipeline=lambda: "ok")
    return TestClient(app, base_url=f"https://{host}", follow_redirects=False)


def token_of(c):
    return c.get("/").text.split('name="csrf_token" value="')[1].split('"')[0]


def test_tailnet_host_gets_the_page(tmp_path, monkeypatch):
    assert make(tmp_path, monkeypatch, HOST).get("/").status_code == 200


def test_unknown_host_is_400(tmp_path, monkeypatch):
    assert make(tmp_path, monkeypatch, HOST, host="evil.example.com").get("/").status_code == 400


def test_without_the_variable_the_tailnet_host_is_400(tmp_path, monkeypatch):
    assert make(tmp_path, monkeypatch, None).get("/").status_code == 400


def test_post_from_the_tailnet_origin_works_and_other_origin_is_403(tmp_path, monkeypatch):
    c = make(tmp_path, monkeypatch, HOST)
    t = token_of(c)
    assert c.post("/run", data={"csrf_token": t}, headers={"origin": f"https://{HOST}"}).status_code == 303
    assert c.post("/run", data={"csrf_token": t}, headers={"origin": "https://evil.example"}).status_code == 403


def test_theme_button_works_from_the_tailnet_host(tmp_path, monkeypatch):
    c = make(tmp_path, monkeypatch, HOST)
    r = c.post("/theme", data={"csrf_token": token_of(c), "theme": "dark"}, headers={"origin": f"https://{HOST}"})
    assert r.status_code == 303 and "theme=dark" in r.headers["set-cookie"]


def test_demo_ignores_the_variable(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ALLOWED_HOSTS_EXTRA", HOST)
    app = web.create_app(str(tmp_path / "d.db"), dict(CFG), base_dir=tmp_path, log_path=str(tmp_path / "l.jsonl"), demo=True)
    assert TestClient(app, base_url=f"https://{HOST}").get("/").status_code == 400


@pytest.mark.parametrize("raw,want", [
    ("", []), (" , ,", []), (" Box.Example-Tailnet.ts.net ", [HOST]), ("a.ts.net,,B.ts.net", ["a.ts.net", "b.ts.net"]),
    ("*", []), ("*.ts.net,box.ts.net", ["box.ts.net"]),
])
def test_parsing(raw, want):
    assert web.extra_hosts({"ALLOWED_HOSTS_EXTRA": raw}) == want
