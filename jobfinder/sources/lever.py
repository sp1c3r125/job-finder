# https://github.com/lever/postings-api  GET /v0/postings/SITE?mode=json  (EU: api.eu.lever.co)
from ..models import Job
from ..text import to_iso

URL = "https://api.lever.co/v0/postings/{board}"
EU_URL = "https://api.eu.lever.co/v0/postings/{board}"


def fetch(http, company):
    base = EU_URL if company.get("region") == "eu" else URL
    data = http.get_json(base.format(board=company["board"]), {"mode": "json"})
    jobs = []
    for j in data:
        cats = j.get("categories") or {}
        loc = cats.get("location", "")
        wt = j.get("workplaceType")
        remote = {"remote": True, "on-site": False, "hybrid": False}.get(wt)
        if remote is None and "remote" in loc.lower():
            remote = True
        jobs.append(Job("lever", j["id"], company["name"], j["text"], j["hostedUrl"],
                        to_iso(j.get("createdAt")), loc, remote, j.get("descriptionPlain", "")))
    return jobs
