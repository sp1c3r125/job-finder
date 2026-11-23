from datetime import datetime, timedelta, timezone

import pytest

from jobfinder.models import Job

CFG = {"max_age_days": 3, "keywords": ["AI", "automation", "agent", "LLM", "forward deployed", "solutions engineer"],
       "location_allow": ["philippines", "apac", "asia", "worldwide", "anywhere"], "score_threshold": 70,
       "user_agent": "test", "request_delay_seconds": 0}


TARGET = {"titles": ["AI Automation Engineer", "Solutions Engineer"], "title_words": ["engineer", "automation", "solutions"],
          "exclude_words": ["trainer", "talent pool", "annotator", "intern"], "level": "mid"}


def recent(days=1):
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()


def make_job(**kw):
    d = dict(source="ashby", source_id="1", company="Acme", title="AI Automation Engineer", url="https://x/1",
             posted=recent(), location="Remote - Worldwide", remote=True, description="Build LLM agents.")
    d.update(kw)
    return Job(**d)


class FakeHttp:
    """Stands in for HttpClient: maps url -> payload. Records calls."""
    def __init__(self, payloads):
        self.payloads, self.calls = payloads, []

    def get_json(self, url, params=None):
        self.calls.append((url, params))
        return self.payloads[url]

    def get_text(self, url, params=None):
        self.calls.append((url, params))
        return self.payloads[url]


class FakeBackend:
    """Mocked model client. Returns queued responses in order; Exception instances are raised."""
    def __init__(self, responses):
        self.responses, self.calls = list(responses), []

    def complete(self, system, user, schema=None, tool_name=None, max_tokens=None, tools=None, timeout=None):
        self.calls.append((system, user))
        self.tools = getattr(self, "tools", []) + [tools]
        r = self.responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


GOOD = {"fit_score": 82, "matching_evidence": ["Built n8n workflows"], "gaps": ["No Kubernetes"],
        "deal_breakers": [], "summary": "Good fit."}


@pytest.fixture
def logs():
    events = []
    return events, lambda event, job=None, **f: events.append((event, f))


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """Tests never reach the network or a model: only loopback connects are allowed."""
    import socket
    real = socket.socket.connect

    def guarded(self, address, *a, **k):
        host = address[0] if isinstance(address, tuple) else ""
        if host not in ("127.0.0.1", "::1", "localhost"):
            raise RuntimeError(f"network blocked in tests: {host}")
        return real(self, address, *a, **k)
    monkeypatch.setattr(socket.socket, "connect", guarded)
