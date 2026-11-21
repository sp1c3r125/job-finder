"""Polite HTTP: user agent, 1 request/sec, per-day file cache, 429 backoff."""
import hashlib
import json
import time
from datetime import date
from pathlib import Path

import requests

CACHE_DIR = Path(".cache")
_last_request = 0.0


class HttpClient:
    def __init__(self, user_agent, delay=1.0, cache_dir=CACHE_DIR, max_retries=3, sleep=time.sleep):
        self.headers = {"User-Agent": user_agent}
        self.delay = delay
        self.cache_dir = Path(cache_dir)
        self.max_retries = max_retries
        self.sleep = sleep

    def _cache_path(self, url, params):
        key = hashlib.sha256(json.dumps([url, params], sort_keys=True).encode()).hexdigest()[:24]
        return self.cache_dir / date.today().isoformat() / f"{key}.txt"

    def get_text(self, url, params=None):
        global _last_request
        path = self._cache_path(url, params)
        if path.exists():
            return path.read_text(encoding="utf-8")
        for attempt in range(self.max_retries + 1):
            wait = self.delay - (time.monotonic() - _last_request)
            if wait > 0:
                self.sleep(wait)
            _last_request = time.monotonic()
            resp = requests.get(url, params=params, headers=self.headers, timeout=30)
            if resp.status_code == 429 and attempt < self.max_retries:
                retry_after = resp.headers.get("Retry-After", "")
                self.sleep(int(retry_after) if retry_after.isdigit() else 60 * (attempt + 1))
                continue
            resp.raise_for_status()
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(resp.text, encoding="utf-8")
            return resp.text
        raise RuntimeError("unreachable")

    def get_json(self, url, params=None):
        return json.loads(self.get_text(url, params))
