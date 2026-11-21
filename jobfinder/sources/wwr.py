# Official RSS feeds listed at https://weworkremotely.com/remote-job-rss-feed
# Terms: free to use with attribution (links back to We Work Remotely).
# Item title is "Company: Job title"; <region> is e.g. "Anywhere in the World", "USA Only".
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime

from ..models import Job
from ..text import html_to_text, to_iso

FEEDS = [
    "https://weworkremotely.com/categories/remote-programming-jobs.rss",
    "https://weworkremotely.com/categories/all-other-remote-jobs.rss",
]


def _date(s):
    try:
        return to_iso(parsedate_to_datetime(s).isoformat())
    except (TypeError, ValueError):
        return ""


def fetch(http, cfg, log=None):
    seen, jobs = set(), []
    for feed in FEEDS:
        root = ET.fromstring(http.get_text(feed))
        for item in root.iter("item"):
            link = (item.findtext("link") or "").strip()
            if not link or link in seen:
                continue
            seen.add(link)
            company, sep, title = (item.findtext("title") or "").partition(": ")
            if not sep:
                company, title = "", company
            jobs.append(Job("wwr", item.findtext("guid") or link, company.strip(), title.strip(), link,
                            _date(item.findtext("pubDate")), (item.findtext("region") or "").strip(), True,
                            html_to_text(item.findtext("description") or "")))
    return jobs
