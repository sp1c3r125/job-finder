import pytest

from jobfinder import settings, web

CFG = {"hosted_sources": ["greenhouse"]}
GOOD = {
    "JOBFINDER_MODE": "hosted", "BACKEND": "api",
    "DATABASE_URL": "postgresql://invented", "SUPABASE_URL": "https://invented.example",
    "SUPABASE_ANON_KEY": "anon-invented", "ANTHROPIC_API_KEY": "key-invented",
    "SESSION_SECRET": "secret-invented", "ALLOWED_HOSTS_EXTRA": "jobs.example.test", "SUPPORT_CONTACT": "help@example.test",
}


def test_default_mode_is_local():
    assert settings.mode({}) == "local"
    assert settings.check_startup({}, {}) == "local"


def test_unknown_mode_is_an_error():
    with pytest.raises(SystemExit, match="JOBFINDER_MODE"):
        settings.mode({"JOBFINDER_MODE": "cloud"})


def test_hosted_with_all_settings_is_ready():
    assert settings.hosted_problems(GOOD, CFG) == []
    assert settings.check_startup(GOOD, CFG) == "hosted"


@pytest.mark.parametrize("name", sorted(settings.HOSTED_REQUIRED))
def test_hosted_names_each_missing_setting(name):
    env = {k: v for k, v in GOOD.items() if k != name}
    with pytest.raises(SystemExit) as e:
        settings.check_startup(env, CFG)
    assert name in str(e.value)


def test_hosted_lists_every_problem_and_never_prints_values():
    with pytest.raises(SystemExit) as e:
        settings.check_startup({"JOBFINDER_MODE": "hosted", "SESSION_SECRET": "do-not-print"}, {})
    text = str(e.value)
    assert "DATABASE_URL" in text and "ANTHROPIC_API_KEY" in text and "hosted_sources" in text
    assert "do-not-print" not in text


def test_hosted_refuses_claude_code_backend():
    assert any("BACKEND" in p for p in settings.hosted_problems({**GOOD, "BACKEND": "claude-code"}, CFG))


def test_hosted_default_backend_is_api():
    env = {k: v for k, v in GOOD.items() if k != "BACKEND"}
    assert settings.hosted_problems(env, CFG) == []


def test_hosted_refuses_demo_and_empty_source_list():
    assert any("DEMO" in p for p in settings.hosted_problems({**GOOD, "DEMO": "1"}, CFG))
    assert any("hosted_sources" in p for p in settings.hosted_problems(GOOD, {"hosted_sources": []}))


def test_hosted_mode_serves_the_hosted_app_not_the_local_one(monkeypatch, tmp_path):
    """The local app has no login. In hosted mode create_app must hand back the signed-in app, which has no /files route."""
    from fastapi.testclient import TestClient
    for k, v in GOOD.items():
        monkeypatch.setenv(k, v)
    app = web.create_app(str(tmp_path / "t.db"), {"hosted_sources": ["greenhouse"]}, base_dir=tmp_path)
    c = TestClient(app, base_url="https://jobs.example.test", follow_redirects=False)
    assert c.get("/").status_code == 303 and c.get("/").headers["location"] == "/login"
    assert c.get("/files/drafts/x.md").status_code in (404, 405)


def test_app_refuses_hosted_mode_without_settings(monkeypatch, tmp_path):
    monkeypatch.setenv("JOBFINDER_MODE", "hosted")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(SystemExit, match="DATABASE_URL"):
        web.create_app(str(tmp_path / "t.db"), {}, base_dir=tmp_path)


def test_load_env_does_not_override_existing(monkeypatch, tmp_path):
    f = tmp_path / ".env"
    f.write_text('A_SET=from-file\nB_NEW="quoted"\n# c=1\n', encoding="utf-8")
    monkeypatch.setenv("A_SET", "already")
    monkeypatch.delenv("B_NEW", raising=False)
    settings.load_env(str(f))
    import os
    assert os.environ["A_SET"] == "already" and os.environ["B_NEW"] == "quoted"
    monkeypatch.delenv("B_NEW", raising=False)


@pytest.mark.parametrize("source", ["himalayas", "remoteok", "wwr", "research", "somewhere-new"])
def test_hosted_refuses_any_source_outside_the_allowlist(source):
    problems = settings.hosted_problems(GOOD, {"hosted_sources": ["greenhouse", source]})
    assert any(source in p and "may not fetch" in p for p in problems)
    with pytest.raises(SystemExit, match=source):
        settings.check_startup(GOOD, {"hosted_sources": [source]})


def test_hosted_allows_exactly_the_three_ats_sources():
    assert settings.HOSTED_SOURCES_ALLOWED == ("greenhouse", "lever", "ashby")
    assert settings.hosted_problems(GOOD, {"hosted_sources": ["greenhouse", "lever", "ashby"]}) == []


def test_local_mode_is_not_limited_by_the_allowlist():
    assert settings.check_startup({}, {"hosted_sources": ["himalayas"], "aggregators": ["himalayas", "remoteok", "wwr"]}) == "local"
