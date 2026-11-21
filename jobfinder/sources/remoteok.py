# https://remoteok.com/api  First element is {"last_updated", "legal"} (terms): skipped and logged.
# Terms: link back (follow) to the Remote OK URL and name Remote OK as the source; no logo use.
# The feed is ~24h delayed and many listings are not truly remote, so location is judged strictly downstream.
import re

from ..models import Job
from ..text import html_to_text, to_iso

URL = "https://remoteok.com/api"


def fetch(http, cfg, log=None):
    data = http.get_json(URL)
    if data and "legal" in data[0]:
        if log:
            log("remoteok_terms", None, last_updated=data[0].get("last_updated"), legal=data[0]["legal"])
        data = data[1:]
    jobs = []
    for j in data:
        loc = (j.get("location") or "").strip()
        # Remote OK lists some office/hybrid roles; only a clear on-site marker in location/title is rejected here.
        onsite = re.search(r"(?i)\b(hybrid|on-?site|in-office)\b", f"{loc} {j.get('position', '')}")
        jobs.append(Job("remoteok", str(j["id"]), j["company"], j["position"], j["url"],
                        to_iso(j.get("date") or j.get("epoch")), loc, not onsite,
                        html_to_text(j.get("description", ""))))
    return jobs
