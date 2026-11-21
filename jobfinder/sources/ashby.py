# https://developers.ashbyhq.com/docs/public-job-posting-api
# GET https://api.ashbyhq.com/posting-api/job-board/{JOB_BOARD_NAME}
from ..models import Job
from ..text import to_iso

URL = "https://api.ashbyhq.com/posting-api/job-board/{board}"


def fetch(http, company):
    data = http.get_json(URL.format(board=company["board"]))
    jobs = []
    for j in data.get("jobs", []):
        if j.get("isListed") is False:
            continue
        loc = j.get("location", "")
        extra = [s.get("location", "") for s in j.get("secondaryLocations") or []]
        if extra:
            loc = " | ".join([loc] + extra)
        # isRemote is true for Hybrid too, so use workplaceType when present.
        wt = j.get("workplaceType")
        remote = (wt == "Remote") if wt else j.get("isRemote")
        jobs.append(Job("ashby", j["id"], company["name"], j["title"], j["jobUrl"],
                        to_iso(j.get("publishedAt")), loc, remote, j.get("descriptionPlain", "")))
    return jobs
