# https://himalayas.app/docs/remote-jobs-api  (+ https://himalayas.app/docs/openapi.json)
# GET https://himalayas.app/jobs/api/search  params: q, country, worldwide, timezone, sort, page (1-based, 20/page)
# 429: HttpClient waits for Retry-After. Terms: visible credit to Himalayas, use applicationLink as the link,
# do not pass the data to other job boards. Personal use only.
from datetime import datetime, timedelta, timezone

from ..models import Job
from ..text import html_to_text, to_iso

URL = "https://himalayas.app/jobs/api/search"


def fetch(http, cfg, log=None):
    h = cfg.get("himalayas", {})
    cutoff = datetime.now(timezone.utc) - timedelta(days=cfg["max_age_days"])
    seen, jobs = set(), []
    for kw in cfg["keywords"]:
        for page in range(1, h.get("max_pages", 3) + 1):
            data = http.get_json(URL, {"q": kw, "sort": "recent", "country": h.get("country", "PH"),
                                       "worldwide": "true", "timezone": h.get("timezone", "UTC+8"), "page": page})
            batch = data.get("jobs", [])
            if not batch:
                break
            for j in batch:
                if j["guid"] in seen:
                    continue
                seen.add(j["guid"])
                restr = [r.get("name", "") if isinstance(r, dict) else str(r) for r in j.get("locationRestrictions") or []]
                loc = "Worldwide" if not restr else ", ".join(restr)     # empty list means worldwide
                jobs.append(Job("himalayas", j["guid"], j["companyName"], j["title"], j["applicationLink"],
                                to_iso(j.get("pubDate")), loc, True, html_to_text(j.get("description", ""))))
            # sorted by recent: once a whole page is older than the window, later pages are too
            if all(datetime.fromisoformat(to_iso(j.get("pubDate")) or "1970-01-01T00:00:00+00:00") < cutoff for j in batch):
                break
    return jobs
