import requests

from jobfinder import http as h


class Resp:
    def __init__(self, code, text="{}", headers=None):
        self.status_code, self.text, self.headers = code, text, headers or {}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(str(self.status_code))


def test_429_backoff_then_cache(tmp_path, monkeypatch):
    seq = [Resp(429, headers={"Retry-After": "7"}), Resp(200, '{"a": 1}')]
    calls = []
    monkeypatch.setattr(h.requests, "get", lambda *a, **k: calls.append(k["headers"]) or seq.pop(0))
    sleeps = []
    c = h.HttpClient("ua-test", delay=0, cache_dir=tmp_path, sleep=sleeps.append)
    assert c.get_json("https://e/x") == {"a": 1}
    assert 7 in sleeps and calls[0]["User-Agent"] == "ua-test"
    assert c.get_json("https://e/x") == {"a": 1}   # served from the day cache
    assert len(calls) == 2
