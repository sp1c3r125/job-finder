import json
from types import SimpleNamespace

import pytest

from jobfinder.backends import ClaudeCodeBackend


def fake_run(returncode, stdout, stderr):
    return lambda cmd, **kw: SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)


def test_nonzero_exit_keeps_stdout_and_stderr_text_but_not_the_prompt():
    out = json.dumps({"is_error": True, "result": "Not logged in"})
    b = ClaudeCodeBackend(run=fake_run(1, out, ""))
    with pytest.raises(RuntimeError) as e:
        b.complete("SECRET-SYSTEM-PROMPT", "SECRET-USER-TEXT")
    msg = str(e.value)
    assert "claude exited 1" in msg and "Not logged in" in msg
    assert "SECRET" not in msg


def test_nonzero_exit_text_is_cut_to_300_chars_each():
    b = ClaudeCodeBackend(run=fake_run(2, "o" * 1000, "e" * 1000))
    with pytest.raises(RuntimeError) as e:
        b.complete("s", "u")
    assert str(e.value).count("o") <= 300 + len("stdout") and str(e.value).count("e") <= 300 + 40
