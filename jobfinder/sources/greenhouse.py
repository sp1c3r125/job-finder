# https://docs.greenhouse.io/job-board.html  GET /v1/boards/{board_token}/jobs?content=true
from ..models import Job
from ..text import html_to_text, to_iso

URL = "https://boards-api.greenhouse.io/v1/boards/{board}/jobs"


def fetch(http, company):
    data = http.get_json(URL.format(board=company["board"]), {"content": "true"})
    jobs = []
    for j in data.get("jobs", []):
        loc = (j.get("location") or {}).get("name", "")
        desc = html_to_text(j.get("content", ""))
        # Greenhouse has no remote flag; "remote" in location text or description is the signal.
        remote = "remote" in loc.lower() or "remote" in desc[:600].lower() or None
        jobs.append(Job("greenhouse", str(j["id"]), company["name"], j["title"], j["absolute_url"],
                        to_iso(j.get("first_published") or j.get("updated_at")), loc, remote, desc))
    return jobs
