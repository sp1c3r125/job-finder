from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_gitattributes_rules():
    rules = {ln.strip() for ln in (ROOT / ".gitattributes").read_text(encoding="utf-8").splitlines()}
    assert "* text=auto" in rules
    assert "*.cmd text eol=crlf" in rules
    assert "*.xml -text" in rules
    for ext in ("pdf", "docx", "xlsx", "woff2", "db"):
        assert f"*.{ext} binary" in rules


def test_ci_runs_pytest_on_push_and_pr():
    wf = (ROOT / ".github" / "workflows" / "test.yml").read_text(encoding="utf-8")
    assert "windows-latest" in wf and "pytest" in wf and "push" in wf and "pull_request" in wf


def test_readme_owner_step_and_cover_note_wording():
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "BACKEND=claude-code" in text and ".env" in text
    assert "draft a short cover note for the best" not in text
    assert "each job that gets a cover note" not in text
    assert "Drafts only for score >= 70" not in text


def test_network_is_blocked_in_tests():
    import pytest
    import requests
    with pytest.raises(Exception, match="network blocked"):
        requests.get("http://example.com", timeout=2)
